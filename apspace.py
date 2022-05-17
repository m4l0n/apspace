import aiohttp
import aiohttp.web
import asyncio
from bs4 import BeautifulSoup
import utils
import logging
import os
import arrow


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


class APSpace:
    def __init__(self) -> None:
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
        self.ticket = None
        self.intake = None
        self.current_semester = None
        self.session = aiohttp.ClientSession(headers = self.headers)

    async def login(self, credentials: dict) -> str:
        payload = {
            'username': credentials['username'],
            'password': credentials['password']
        }

        ticket_html = await self.session.post(self.ticket_url, data = payload)
        if ticket_html.status == 201:
            soup = BeautifulSoup(await ticket_html.text(), "lxml")
            self.ticket = soup.find("form").get('action').replace('https://cas.apiit.edu.my/cas/v1/tickets/', '')
            logger.info("Logged in to APSpace!")
            await self.load_details()
        else:
            # Must catch this exception
            logger.error("APSpace Credentials Invalid!")
            raise CredentialsInvalid(ticket_html.json()['authentication_exceptions'][1][0])

    async def load_details(self):
        self.intake = await self.get_intake_details("current_intake")
        self.current_semester = await self.get_current_semester()

    async def take_attendance(self, otp: str) -> str:
        if (len(otp) != 3):
            logger.debug("OTP does not match required format!")
            # Must catch this exception
            raise OTPError("OTP Format Invalid!")
        else:
            return await self.sign_otp(int(otp))

    async def sign_otp(self, otp: int) -> str:
        """
        Sends a POST request with the otp and returns the status.

        Parameters
        ----------
        otp : int
        """
        auth_ticket = await self.get_service_auth("attendix")
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
        otp_response = await self.session.post("https://attendix.apu.edu.my/graphql", json = payload, headers = headers)
        if otp_response.status == 200:
            otp_response = await otp_response.json()
            if (otp_response['data'] == "null" or not otp_response['data']):
                logger.error(otp_response['errors'][0]['message'])
                raise OTPError(otp_response['errors'][0]['message'])
            else:
                if (otp_response['data']['updateAttendance']['attendance'] == "Y"):
                    logger.info("Successfully signed attendance!")
                    class_code = otp_response['data']['updateAttendance']['classcode']
                    return class_code
        elif otp_response.status == 401:
            print(await otp_response.json()['errors'][0]['message'])

    async def get_attendance_percentage(self) -> float:
        """
        Gets header and ticket (API authentication) from get_service_ticket() function.

        Parse all attendance percentage from every module in second semester and counts average percentage.

        Returns
        ----------
        total_attendance / count : Average attendance percentage of the semester
        """
        auth_ticket = await self.get_service_auth("student/attendance")
        query = {
            'intake': self.intake,
            'ticket': auth_ticket
        }
        attendance_url = self.apiit_url_builder(service_name = "attendance", query = query)
        response = await self.session.get(attendance_url)
        if response.status == 200:
            logger.debug("Request for attendance percentage successful!")
            total_attendance, count = 0, 0
            for course in await response.json():
                if (course['SEMESTER'] == self.current_semester):
                    total_attendance += course['PERCENTAGE']
                    count += 1
            return round(total_attendance / count, 2)
        elif response.status == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            raise aiohttp.web.HTTPUnauthorized(reason = "Unauthorised", text = "APSpace Auth Ticket is invalid!")

    async def get_semester_details(self, intake_code=None):
        auth_ticket = await self.get_service_auth("student/sub_and_course_details")
        query = {
            'intake': self.intake if intake_code is None else intake_code,
            'ticket': auth_ticket
        }
        sub_and_course_url = self.apiit_url_builder(service_name = "sub_and_course_details", query = query)
        response = await self.session.get(sub_and_course_url)
        if response.status == 200:
            logger.debug("Request for semester details sucessful!")
            temp = len(await response.json())
            match temp:
                case 1:
                    self.current_semester = 1
                    prev_intake_code = await self.get_intake_details("previous_intake")
                    if prev_intake_code:
                        return await self.get_semester_details(prev_intake_code)
                    else:
                        cgpa = 0.00
                case 2:
                    self.current_semester = 2
                    cgpa = await response.json()[-2]['IMMIGRATION_GPA']
            return self.current_semester, cgpa
        elif response.status == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            raise aiohttp.web.HTTPUnauthorized(reason = "Unauthorised", text = "APSpace Auth Ticket is invalid!")

    async def get_current_semester(self, intake_code=None):
        auth_ticket = await self.get_service_auth("student/sub_and_course_details")
        query = {
            'intake': self.intake if intake_code is None else intake_code,
            'ticket': auth_ticket
        }
        sub_and_course_url = self.apiit_url_builder(service_name = "sub_and_course_details", query = query)
        response = await self.session.get(sub_and_course_url)
        if response.status == 200:
            logger.debug("Request for semester number sucessful!")
            temp = len(await response.json())
            match temp:
                case 1:
                    current_semester = 1
                case 2:
                    current_semester = 2
            return current_semester
        elif response.status == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            raise aiohttp.web.HTTPUnauthorized(reason = "Unauthorised", text = "APSpace Auth Ticket is invalid!")

    async def get_intake_details(self, query_type):
        auth_ticket = await self.get_service_auth("student/courses")
        query = {
            'ticket': auth_ticket
        }
        courses_url = self.apiit_url_builder(service_name = "courses", query = query)
        response = await self.session.get(courses_url)
        if response.status == 200:
            logger.debug("Request for courses details successful!")
            response = await response.json()
            match query_type:
                case "previous_intake":
                    if len(response) > 1:
                        return response[-1]['INTAKE_CODE']
                    else:
                        return None
                case "current_intake":
                    return response[0]['INTAKE_CODE']
                case "course_name":
                    return response[0]['COURSE_DESCRIPTION']
                case "course_type":
                    return response[0]['TYPE_OF_COURSE']
                case "all_current":
                    return response[0]['INTAKE_CODE'], response[0]['COURSE_DESCRIPTION'], \
                           response[0]['TYPE_OF_COURSE']
        elif response.status == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            raise aiohttp.web.HTTPUnauthorized(reason = "Unauthorised", text = "APSpace Auth Ticket is invalid!")

    async def get_my_modules(self):
        auth_ticket = await self.get_service_auth("student/attendance")
        query = {
            'intake': self.intake,
            'ticket': auth_ticket
        }
        attendance_url = self.apiit_url_builder(service_name = "attendance", query = query)
        response = await self.session.get(attendance_url)
        if response.status == 200:
            logger.debug("Request for semester modules successful!")
            modules = [course['MODULE_ATTENDANCE'] for course in await response.json()
                       if (course['SEMESTER'] == self.current_semester)]
            return modules
        elif response.status == 401:
            logger.error("APSpace Auth Ticket is invalid!")
            raise aiohttp.web.HTTPUnauthorized(reason = "Unauthorised", text = "APSpace Auth Ticket is invalid!")

    async def get_weekly_timetable(self):
        timetable_url = "https://s3-ap-southeast-1.amazonaws.com/open-ws/weektimetable"
        response = await self.session.get(timetable_url)
        if response.status_code == 200:
            response = await response.json()
            semester_modules = await self.get_my_modules()
            semester_modules = [name.title() for name in semester_modules]
            for schedule in response:
                if (schedule['MODULE_NAME'].replace('&', 'And').title() in semester_modules and
                        schedule['INTAKE'] == self.intake and
                        arrow.get(schedule['TIME_FROM_ISO']) > arrow.now('Asia/Kuala_Lumpur')):
                    yield schedule
        else:
            logger.critical("Something went wrong when requesting for weekly timetable!")

    def apiit_url_builder(self, service_name, query):
        query_string = []
        for key in query.keys():
            query_string.append(
                f'{key}={query[key]}'
            )
        return f'{self.apiit_url}{service_name}?{"&".join(query_string)}'

    async def get_service_auth(self, service_name: str) -> str:
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
        response = await self.session.post(service_ticket_url, data = "")
        service_ticket = await response.text()
        return service_ticket


async def main():
    apspace_session = APSpace()
    await apspace_session.login('credentials')
    try:
        print(await apspace_session.sign_otp(111))
    except OTPError as e:
        print(e.message)
    finally:
        await apspace_session.session.close()


if __name__ == "__main__":
    # API_KEY = ''
    # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
