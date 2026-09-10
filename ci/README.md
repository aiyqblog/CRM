# 本地流水线执行器

把 GitLab CI 里定义的同一条链路，在本地用**同一个可执行文件**跑一遍。
存在的理由：CI 跑挂了要等推送 + 队列，反馈周期几分钟；
本地跑一遍只要几十秒，是「跑挂了自己修、修完自己重跑」能成立的前提。

    python ci/run_pipeline.py                # 全链路
    python ci/run_pipeline.py --only lint unit
    python ci/run_pipeline.py --from api     # 从某个阶段开始
    python ci/run_pipeline.py --no-docker    # 跳过构建与部署
    python ci/run_pipeline.py --list         # 查看阶段列表

报告写入 ``ci-report.json``，供后续自动定位失败使用。
