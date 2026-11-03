[app]
title = MEXC Pro Scalper
package.name = mexcproscalper
package.domain = org.mexcscalper
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,db
version = 1.0.0

requirements = python3,kivy==2.3.0,requests,urllib3,charset-normalizer,idna,certifi,arabic-reshaper,python-bidi,openssl,sqlite3

orientation = portrait,landscape
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True
p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 0
