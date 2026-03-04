import os

timeout = 180
workers = 1
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
reload = False
loglevel = "warning"
accesslog = "-"
keepalive = 2
preload_app = False
