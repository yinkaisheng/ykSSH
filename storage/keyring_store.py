#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from storage.credential_store import CredentialStore

# Backward-compatible alias; passwords live in config/credentials.json.
KeyringStore = CredentialStore

__all__ = ['CredentialStore', 'KeyringStore']
