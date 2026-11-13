# -*- coding: utf-8 -*-
"""EZcode 版本号与更新源的「唯一事实源」。

以前版本散落在三处: build/ezcode_common.py 的 VERSION=1.0.0、
app/webui/index.html 的 v44、build/version_info.txt 的 1.0.0.0。
现在客户端、安装器、界面、更新检查、发布脚本全部只读本文件；
发新版只改 APP_VERSION / APP_BUILD 两行(构建时会自动同步其余文件)。
"""
APP_NAME = "EZcode"
APP_VERSION = "1.0.50"
APP_BUILD = 50

# GitHub 更新源默认仓库(owner/repo)。留空时客户端不报错、只提示"请填写仓库",
# 在「设置中心 → 版本更新」里填一次即永久生效(写入本机 devices.json, 升级不覆盖)。
# 也可用环境变量 EZCODE_GITHUB_REPO 覆盖, 便于内网批量部署。
GITHUB_REPO = "0to1-labs/EZcode"

# 国内服务器源的默认地址; 建好服务器后在设置中心填 url 并启用即可,
# 两条源会同时检查、自动取最高版本, 不需要改代码。
STATIC_UPDATE_URL = ""
