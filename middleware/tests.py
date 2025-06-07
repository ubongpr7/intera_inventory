from django.test import TestCase,override_settings
from django.test.client import Client
from http import HTTPStatus

class IPBlackListMiddlewareTest(TestCase):

    def setup(self):
        self.client= Client()
        
    @override_settings(BANNED_IPS=None)
    def test_request_successful_without_blacklist_setting(self):
        response= self.client.get('/admin')
        self.assertEqual(response.status_code,HTTPStatus.OK)

    @override_settings(BANNED_IPS=['127.0.1'])
    def test_request_successful_with_allowed_ip(self):
        response= self.client.get('/admin',REMOTE_ADDR='127.0.3')
        self.assertEqual(response.status_code,HTTPStatus.OK)

    @override_settings(BANNED_IPS=['127.0.1'])
    def test_request_unsuccessful_with_blacklist_setting(self):
        response= self.client.get('/admin',REMOTE_ADDR='127.0.1')
        self.assertEqual(response.status_code,HTTPStatus.FORBIDDEN)
