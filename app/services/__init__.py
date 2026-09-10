"""业务服务层。

刻意不在本文件顶层导入子模块 —— 子模块之间存在互相引用（visit 用
permission，project 用 gate，report 用 permission），在包初始化时集中导入
会形成循环依赖。调用方按需 ``from app.services import visit`` 即可。

分层约定：
    - ``*_rules`` / 纯函数    → 单元测试目标，不碰数据库
    - 编排函数（接受 Session） → 接口测试目标
"""
