
import threading
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

class EmailThread(threading.Thread):
    def __init__(self,email_message):
        self.email_message=email_message
        threading.Thread.__init__(self)
    def run(self):
        print("Initializing thread")
        self.email_message.send()
        if self.email_message.send():
            print('done')

def send_html_email(subject, message,  to_email,html_file):
    html_content = render_to_string(html_file, {'subject': subject, 'message': message})
    text_content = strip_tags(html_content)

    msg = EmailMultiAlternatives(subject, text_content,  settings.EMAIL_HOST_USER, to_email)
    msg.attach_alternative(html_content, "text/html")
    print("initials")
    EmailThread(msg).start()

