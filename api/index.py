import os
import sys

# Add project root to Python path so that the `ppw` package is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ppw.settings.vercel")

from django.core.wsgi import get_wsgi_application

app = get_wsgi_application()
