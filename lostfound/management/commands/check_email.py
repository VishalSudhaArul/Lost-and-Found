"""Diagnose email delivery: `python manage.py check_email [--send-to ADDRESS]`.

Never prints the password. Without --send-to it only tests the login."""
from django.core.management.base import BaseCommand, CommandError

from lostfound.services import mailer


class Command(BaseCommand):
    help = "Check SMTP settings and login; optionally send a test email."

    def add_arguments(self, parser):
        parser.add_argument("--send-to", help="Also send a test email to this address.")

    def handle(self, *args, **opts):
        ok, message = mailer.diagnose()
        (self.stdout.write(self.style.SUCCESS(message)) if ok else self.stderr.write(self.style.ERROR(message)))
        if not ok:
            raise CommandError("Email delivery is not working.")
        if opts["send_to"]:
            sent = mailer.send("Campus Lost & Found: test email",
                               "This is a test email from Campus Lost & Found. Email delivery is working.",
                               opts["send_to"], purpose="test")
            if not sent:
                raise CommandError("Login worked but sending failed; see the log line above.")
            self.stdout.write(self.style.SUCCESS("Test email sent."))
