# -*- coding: utf-8 -*-
"""PyInstaller 打包入口。
正常双击 → 启动桌面客户端; 带 --uninstall → 走卸载流程(卸载快捷方式指向此参数)。
"""
import os
import sys

if "--uninstall" in sys.argv:
    import ezcode_uninstall
    ezcode_uninstall.run()
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client  # noqa: E402
client.Client().run()
