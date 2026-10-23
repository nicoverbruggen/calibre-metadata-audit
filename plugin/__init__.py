# SPDX-License-Identifier: GPL-3.0-only
from calibre.customize import InterfaceActionBase


class MetadataAuditPlugin(InterfaceActionBase):
    name = 'Metadata Audit'
    description = 'Compare EPUB metadata with Calibre and apply selected corrections.'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Nico Verbruggen'
    version = (0, 2, 0)
    minimum_calibre_version = (7, 0, 0)
    actual_plugin = 'calibre_plugins.metadata_audit.ui:MetadataAuditAction'
