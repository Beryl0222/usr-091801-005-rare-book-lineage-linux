# 海外古籍研究谱系库

本项目整理海外馆藏中的中华插图古籍，为学术团队提供协作型资料后端。作品、版本、实体卷册、馆藏机构和数字图像分别编号建模；书名异称、残卷关系、刊刻年代与流传路径通过可引用的研究观点表达，不把尚有争议的判断写成固定字段。

## 领域规则

- **观点（claim）**：断代、作者、套印技法、流传路径四类结论，必须引用可定位的页叶证据（数字图像或卷册页叶）并标注置信程度（高/中/低）。状态机为 `draft → discussion → adopted`，任何非终态可 `withdrawn`；竞争性观点并存，撤回只改状态、不删除历史引用。
- **修订与协作**：观点可经 `revises` 链式修订（版本号递增，旧版标记 `superseded_by`，已过时版本不可再被修订）；评论附于观点；合并建议（merge）被接受后原记录保留并以别名指向存续实体，谱系不断。
- **出版冻结**：正式出版仅可冻结 `adopted` 状态的观点，冻结时把观点、采用版本号与页叶证据（含当时馆藏机构名）整体快照；此后的机构更名、卷册迁移、观点撤回都不改写快照。
- **许可与导出阻断**：许可按用途（research/education/exhibition/publication）、地域、清晰度上限和到期日约束。`POST /exports/check` 逐项检查导出清单，越权图像被阻断并给出固定顺序的原因（无许可记录 / 用途 / 地域 / 清晰度 / 已过期）。
- **谱系连续**：机构更名追加名称事件，卷册迁移追加馆藏事件；自然键索引保证重复导入（含迁移后按原馆藏信息导入）不会产生第二份卷册。
- **反向抽查**：`GET /publications/{id}/audit` 对跨机构出版清单逐条核对采用版本、页叶证据与当前授权状态，并按机构汇总。
- **确定性**：导出与审计为纯函数式接口，`as_of` 日期必须显式给出；同样输入重复请求返回逐字节一致的结果。

## 运行

```bash
python3 service.py --check        # 基础检查（含确定性自检）
python3 service.py --port 8000    # 启动服务（载入样例数据）
python3 service.py --empty        # 空库启动
npm test                          # 全部测试（契约 + 领域 + API）
```

## API 概览

- `GET /health`；`GET /{works|editions|volumes|institutions|images|licenses|claims|comments|merges|publications}[/{id}]`
- `POST /import`：按书目自然键批量导入（机构→作品→版本→卷册→许可→图像），幂等去重
- `POST /claims`、`POST /claims/{id}/transition`、`POST /claims/{id}/comments`
- `POST /merges`、`POST /merges/{id}/resolve`
- `POST /institutions/{id}/rename`、`POST /volumes/{id}/migrate`、`GET /volumes/{id}/lineage`
- `POST /publications`（冻结采用观点）、`GET /publications/{id}/audit?as_of=YYYY-MM-DD[&usage=&region=&resolution=]`
- `POST /exports/check`：`{usage, region, resolution, as_of, image_ids[]}`

## 样例数据

样例涉及《湖山胜概》《无双谱》《帝鉴图说》《新镌海内奇观》在英、法、日、德五家机构的藏本，书目与许可均经 `/import` 作为可查询数据入库（见 `seed.py`），包含竞争断代、观点修订、撤回、机构异译合并、卷册迁移与一份冻结的跨机构出版清单。
