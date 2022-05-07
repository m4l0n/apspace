import requests
from bs4 import BeautifulSoup
import utils
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

API_KEY = os.environ['API_KEY']


class OTPError(Exception):
    """
    An exception class that is raised when the attendance OTP is invalid.

    Attributes
    ----------
    message : str
        Error message string.

    Methods
    -------
    __str__:
        Overwrites str() to return error message string.
    """

    def __init__(self, message, *args, **kwargs):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        """
        Overwrites str() to return error message string.

        Returns
        -------
        self.message : Error message string
        """
        return self.message


class CredentialsInvalid(Exception):
    """
    An exception class that is raised when the credentials is invalid.

    Attributes
    ----------
    message : str
        Error message string.

    Methods
    -------
    __str__:
        Overwrites str() to return error message string.
    """

    def __init__(self, message, *args, **kwargs):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        """
        Overwrites str() to return error message string.

        Returns
        -------
        self.message : Error message string
        """
        return self.message


class APSpace:
    def __init__(self, credentials: dict) -> None:
        self.credentials = credentials
        self.ticket_url = "https://cas.apiit.edu.my/cas/v1/tickets"
        self.apiit_url = "https://api.apiit.edu.my/student/"
        self.headers = {
            'sec-ch-ua': '"Not A;Brand\";v=\"99\", \"Chromium\";v=\"101\", \"Microsoft Edge\";v=\"101"',
            'DNT': '1',
            'sec-ch-ua-mobile': '?0',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/101.0.4951.41 Safari/537.36 Edg/101.0.1210.32',
            'sec-ch-ua-platform': '"Windows"',
            'Origin': 'https://apspace.apu.edu.my',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'Referer': 'https://apspace.apu.edu.my/',
            'Content-type': 'application/x-www-form-urlencoded'
        }
        self.ticket = self.get_ticket()
        self.intake = self.get_intake_details("current_intake")
        self.current_semester = None

    def get_ticket(self) -> str:
        payload = {
            'username': self.credentials['username'],
            'password': self.credentials['password']
        }

        ticket_html = requests.post(self.ticket_url, data = payload, headers = self.headers)
        if ticket_html.status_code == 201:
            soup = BeautifulSoup(ticket_html.text, "lxml")
            ticket = soup.find("form").get('action').replace('https://cas.apiit.edu.my/cas/v1/tickets/', '')
            logger.info("Logged in to APSpace!")
            return ticket
        else:
            # Must catch this exception
            logger.error("APSpace Credentials Invalid!")
            raise CredentialsInvalid(ticket_html.json()['authentication_exceptions'][1][0])

    def take_attendance(self, otp: str) -> str:
        if (len(otp) != 3):
            logger.debug("OTP does not match required format!")
            # Must catch this exception
            raise OTPError("OTP Format Invalid!")
        else:
            return self.sign_otp(int(otp))

    def sign_otp(self, otp: int) -> str:
        """
        Sends a POST request with the otp and returns the status.

        Parameters
        ----------
        otp : int
        """
        auth_ticket = self.get_service_auth("attendix")
        payload = {
            "operationName": "updateAttendance",
            "variables": {
                "otp": f'{otp:03d}'
            },
            "query": "mutation updateAttendance($otp: String!) {updateAttendance(otp: $otp) {id   attendance    "
                     "classcode    date    startTime    endTime    classType    __typename  }}"
        }
        headers = self.headers
        headers.update({
            'ticket': auth_ticket,
            'x-amz-user-agent': 'aws-amplify/2.0.7',
            'x-api-key': API_KEY
        })
        otp_response = requests.post("https://attendix.apu.edu.my/graphql", json = payload, headers = headers).json()
        if (otp_response['data'] == "null" or not otp_response['data']):
            logger.error(otp_response['errors'][0]['message'])
            raise OTPError(otp_response['errors'][0]['message'])
        else:
            if (otp_response['data']['updateAttendance']['attendance'] == "Y"):
                logger.info("Successfully signed attendance!")
                class_code = otp_response['data']['updateAttendance']['classcode']
                return class_code

    def get_attendance_percentage(self) -> float:
        """
        Gets header and ticket (API authentication) from get_service_ticket() function.

        Parse all attendance percentage from every module in second semester and counts average percentage.

        Returns
        ----------
        total_attendance / count : Average attendance percentage of the semester
        """
        auth_ticket = self.get_service_auth("student/attendance")
        query = {
            'intake': self.intake,
            'ticket': auth_ticket
        }
        attendance_url = self.apiit_url_builder(service_name = "attendance", query = query)
        response = requests.get(attendance_url, headers = self.headers)
        if response.status_code == 200:
            logger.debug("Request for attendance percentage successful!")
            total_attendance, count = 0, 0
            for course in response.json():
                if (course['SEMESTER'] == self.current_semester):
                    total_attendance += course['PERCENTAGE']
                    count += 1
            return round(total_attendance / count, 2)
        elif response.status_code == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            self.ticket = self.get_ticket()
            return self.get_attendance_percentage()

    def get_semester_details(self, intake_code=None):
        auth_ticket = self.get_service_auth("student/sub_and_course_details")
        query = {
            'intake': self.intake if intake_code is None else intake_code,
            'ticket': auth_ticket
        }
        sub_and_course_url = self.apiit_url_builder(service_name = "sub_and_course_details", query = query)
        response = requests.get(sub_and_course_url, headers = self.headers)
        if response.status_code == 200:
            logger.debug("Request for semester details sucessful!")
            temp = len(response.json())
            match temp:
                case 1:
                    self.current_semester = 1
                    prev_intake_code = self.get_intake_details("previous_intake")
                    if prev_intake_code:
                        return self.get_semester_details(prev_intake_code)
                    else:
                        cgpa = 0.00
                case 2:
                    self.current_semester = 2
                    cgpa = response.json()[-2]['IMMIGRATION_GPA']
            return self.current_semester, cgpa
        elif response.status_code == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            self.ticket = self.get_ticket()
            return self.get_semester_details()

    def get_intake_details(self, query_type):
        auth_ticket = self.get_service_auth("student/courses")
        query = {
            'ticket': auth_ticket
        }
        courses_url = self.apiit_url_builder(service_name = "courses", query = query)
        response = requests.get(courses_url, headers = self.headers)
        if response.status_code == 200:
            logger.debug("Request for courses details successful!")
            match query_type:
                case "previous_intake":
                    if len(response.json()) > 1:
                        return response.json()[-1]['INTAKE_CODE']
                    else:
                        return None
                case "current_intake":
                    return response.json()[0]['INTAKE_CODE']
                case "course_name":
                    return response.json()[0]['COURSE_DESCRIPTION']
                case "course_type":
                    return response.json()[0]['TYPE_OF_COURSE']
                case "all_current":
                    return response.json()[0]['INTAKE_CODE'], response.json()[0]['COURSE_DESCRIPTION'], \
                           response.json()[0]['TYPE_OF_COURSE']
        elif response.status_code == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            self.ticket = self.get_ticket()
            return self.get_previous_intake()

    def apiit_url_builder(self, service_name, query):
        query_string = []
        for key in query.keys():
            query_string.append(
                f'{key}={query[key]}'
            )
        return f'{self.apiit_url}{service_name}?{"&".join(query_string)}'

    def get_service_auth(self, service_name: str) -> str:
        """
        Gets ticket (API authentication) from API according to the service_name provided (attendix OR student/attendance).

        Parameters
        ----------
        service_name : int

        Returns
        ----------
        service_ticket : Authentication string to API
        """
        service_ticket_url = f'{self.ticket_url}/{self.ticket}?service=https://api.apiit.edu.my/{service_name}'
        service_ticket = requests.post(service_ticket_url, data = "", headers = self.headers).text
        return service_ticket


if __name__ == "__main__":
    try:
        pass
    except CredentialsInvalid as e:
        print(e.message)
