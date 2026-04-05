from django.contrib import admin

from .models import BackupRecord, NodeRedConfig, RestoreRecord

admin.site.register(NodeRedConfig)
admin.site.register(BackupRecord)
admin.site.register(RestoreRecord)
