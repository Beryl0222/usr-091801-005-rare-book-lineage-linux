"""样例数据：书目与许可作为可查询数据经导入接口入库，而非硬编码答案。

涵盖《湖山胜概》《无双谱》《帝鉴图说》《新镌海内奇观》四部插图古籍
在英、法、日、德五家机构的藏本、图像与许可样例；观点、修订、评论、
合并、迁移与出版冻结均通过正式业务接口登记，保证样例库与真实使用
路径完全一致。
"""

from domain import Store

BUNDLE = {
    "institutions": [
        {"name": "大英图书馆", "aliases": ["British Library", "英国国家图书馆"]},
        {"name": "法国国家图书馆", "aliases": ["BnF", "Bibliothèque nationale de France"]},
        {"name": "内阁文库", "aliases": ["日本国立公文书馆内阁文库", "Naikaku Bunko"]},
        {"name": "巴伐利亚州立图书馆", "aliases": ["Bayerische Staatsbibliothek", "BSB"]},
        {"name": "柏林国家图书馆", "aliases": ["Staatsbibliothek zu Berlin"]},
        # 与"巴伐利亚州立图书馆"同馆的异译，入库后通过合并建议归并。
        {"name": "巴伐利亚国家图书馆", "aliases": []},
    ],
    "works": [
        {"title": "湖山胜概", "aliases": ["湖山勝概"]},
        {"title": "无双谱", "aliases": ["無雙譜", "南陵无双谱"]},
        {"title": "帝鉴图说", "aliases": ["帝鑑圖說"]},
        {"title": "新镌海内奇观", "aliases": ["海内奇观", "新鎸海內奇觀"]},
    ],
    "editions": [
        {"work": "湖山胜概", "statement": "明万历间彩色套印本（样例著录）"},
        {"work": "无双谱", "statement": "清康熙刻本（样例著录）"},
        {"work": "帝鉴图说", "statement": "明万历刻本（样例著录）"},
        {"work": "新镌海内奇观", "statement": "明万历三十七年武林杨氏刻本（样例著录）"},
    ],
    "volumes": [
        {
            "work": "湖山胜概", "edition": "明万历间彩色套印本（样例著录）",
            "institution": "法国国家图书馆", "shelfmark": "BnF-Chinois-3303",
            "fragment": True, "label": "残存一册",
        },
        {
            "work": "无双谱", "edition": "清康熙刻本（样例著录）",
            "institution": "大英图书馆", "shelfmark": "BL-15333-a-12",
        },
        {
            "work": "新镌海内奇观", "edition": "明万历三十七年武林杨氏刻本（样例著录）",
            "institution": "内阁文库", "shelfmark": "NK-187-0331",
        },
        {
            "work": "帝鉴图说", "edition": "明万历刻本（样例著录）",
            "institution": "巴伐利亚州立图书馆", "shelfmark": "BSB-Cod-sin-123",
        },
        {
            "work": "帝鉴图说", "edition": "明万历刻本（样例著录）",
            "institution": "柏林国家图书馆", "shelfmark": "Berl-Sin-45",
            "fragment": True, "label": "残存半部",
        },
    ],
    "licenses": [
        {
            "institution": "法国国家图书馆", "name": "BnF 研究出版许可（样例）",
            "usages": ["research", "publication"], "regions": ["WORLD"],
            "max_resolution": "high", "expires_on": "2028-12-31",
        },
        {
            "institution": "大英图书馆", "name": "BL 展览出版许可（样例）",
            "usages": ["exhibition", "publication"], "regions": ["GB", "EU"],
            "max_resolution": "medium", "expires_on": "2026-06-30",
        },
        {
            "institution": "内阁文库", "name": "内阁文库研究许可（样例）",
            "usages": ["research"], "regions": ["JP"], "max_resolution": "low",
        },
        {
            "institution": "巴伐利亚州立图书馆", "name": "BSB 出版展览许可（样例）",
            "usages": ["publication", "exhibition"], "regions": ["WORLD"],
            "max_resolution": "high", "expires_on": "2027-03-31",
        },
    ],
    "images": [
        {
            "institution": "法国国家图书馆", "shelfmark": "BnF-Chinois-3303",
            "leaf": "叶一a", "license": "BnF 研究出版许可（样例）", "note": "牌记",
        },
        {
            "institution": "法国国家图书馆", "shelfmark": "BnF-Chinois-3303",
            "leaf": "叶三b", "license": "BnF 研究出版许可（样例）", "note": "西湖全景图",
        },
        {
            "institution": "大英图书馆", "shelfmark": "BL-15333-a-12",
            "leaf": "叶五a", "license": "BL 展览出版许可（样例）", "note": "人物像",
        },
        {
            "institution": "内阁文库", "shelfmark": "NK-187-0331",
            "leaf": "叶二a", "license": "内阁文库研究许可（样例）", "note": "黄山图",
        },
        {
            "institution": "巴伐利亚州立图书馆", "shelfmark": "BSB-Cod-sin-123",
            "leaf": "叶一a", "license": "BSB 出版展览许可（样例）", "note": "进书表",
        },
        # 未登记许可的图像：任何导出都应被阻断。
        {
            "institution": "柏林国家图书馆", "shelfmark": "Berl-Sin-45",
            "leaf": "叶七b", "note": "插图残叶",
        },
    ],
}


def _adopt(store, claim_id):
    store.transition_claim(claim_id, "discussion")
    store.transition_claim(claim_id, "adopted")


def build_seed(store=None):
    """向 store 登记样例库，返回关键实体 id 便于测试与自检。"""
    store = store or Store()
    report = store.import_bundle(BUNDLE)
    ids = {
        "institutions": [item["id"] for item in report["institutions"]],
        "works": [item["id"] for item in report["works"]],
        "editions": [item["id"] for item in report["editions"]],
        "volumes": [item["id"] for item in report["volumes"]],
        "licenses": [item["id"] for item in report["licenses"]],
        "images": [item["id"] for item in report["images"]],
    }
    e_hushan, e_wushuang, e_dijian, _ = ids["editions"]
    w_wushuang = ids["works"][1]
    v_dijian_bsb = ids["volumes"][3]
    v_dijian_berlin = ids["volumes"][4]
    g_paiji, g_xihu, g_renwu, _, g_jinshubiao, _ = ids["images"]

    # 断代：采用说与竞争说并存。
    c1 = store.add_claim(
        "dating", {"type": "edition", "id": e_hushan},
        "《湖山胜概》刊于明万历年间", "高", [{"image_id": g_paiji}], "陈研",
    )
    _adopt(store, c1)
    c2 = store.add_claim(
        "dating", {"type": "edition", "id": e_hushan},
        "《湖山胜概》刊于明崇祯年间", "低", [{"image_id": g_xihu}], "林述",
    )
    store.transition_claim(c2, "discussion")
    store.add_comment(c2, "陈研", "牌记漫漶，崇祯说待考。")

    # 作者：先登记后被修订，出版采用修订版。
    c3 = store.add_claim(
        "authorship", {"type": "work", "id": w_wushuang},
        "《无双谱》图像出自金古良手笔", "高", [{"image_id": g_renwu}], "陈研",
    )
    _adopt(store, c3)

    # 套印技法。
    c4 = store.add_claim(
        "technique", {"type": "edition", "id": e_hushan},
        "采用饾版彩色套印技法", "中", [{"image_id": g_xihu}], "林述",
    )
    _adopt(store, c4)

    # 流传路径：仍在讨论。
    c5 = store.add_claim(
        "transmission", {"type": "volume", "id": v_dijian_bsb},
        "清末自上海流散，二十世纪三十年代入藏巴伐利亚", "中",
        [{"image_id": g_jinshubiao}], "陈研",
    )
    store.transition_claim(c5, "discussion")

    # 撤回的观点：状态改变，证据引用保留。
    c6 = store.add_claim(
        "dating", {"type": "edition", "id": e_dijian},
        "《帝鉴图说》此本刊于明嘉靖年间", "低",
        [{"image_id": g_jinshubiao}], "林述",
    )
    store.transition_claim(c6, "withdrawn")

    # 修订：c7 取代 c3，成为出版的采用版本。
    c7 = store.add_claim(
        "authorship", {"type": "work", "id": w_wushuang},
        "《无双谱》图像为金古良（金史）所绘", "高",
        [{"image_id": g_renwu}], "陈研", revises=c3,
    )
    _adopt(store, c7)

    # 机构异译合并：谱系保留在原记录上。
    merge = store.add_merge(
        "institution", ids["institutions"][5], ids["institutions"][3],
        "同一机构的两种译名（样例）",
    )
    store.resolve_merge(merge, True)

    # 卷册迁移：柏林残卷入藏大英图书馆，谱系事件追加而非改写。
    store.migrate_volume(v_dijian_berlin, ids["institutions"][0], "1937年入藏大英图书馆（样例）")

    # 正式出版：冻结采用观点及其页叶证据快照。
    publication = store.create_publication(
        "海外中华插图古籍图录（样例）", [c1, c4, c7], note="跨机构出版清单样例",
    )
    ids["claims"] = [c1, c2, c3, c4, c5, c6, c7]
    ids["merge"] = merge
    ids["publication"] = publication
    return store, ids


if __name__ == "__main__":
    import json

    seeded_store, seeded_ids = build_seed()
    print(json.dumps(seeded_ids, ensure_ascii=False, indent=2, sort_keys=True))
