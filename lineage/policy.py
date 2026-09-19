"""许可评估：用途、地域、清晰度与有效期四个维度逐项检查。"""

from .model import RESOLUTION_TIERS, require, require_choice, require_date


def validate_request(purpose, region, resolution, as_of):
    require(purpose, "须注明用途 purpose")
    require(region, "须注明地域 region")
    require_choice(resolution, RESOLUTION_TIERS, "所需清晰度")
    require_date(as_of, "评估日期 as_of")


def image_block_reasons(store, image, purpose, region, resolution, as_of):
    """返回阻断理由列表；空列表表示放行。"""
    license_id = image.get("license_id")
    if not license_id:
        return ["图像未关联许可"]
    lic = store.get("license", license_id)
    reasons = []
    if purpose not in lic["purposes"] and "ANY" not in lic["purposes"]:
        reasons.append(f"用途 '{purpose}' 不在许可范围 {lic['purposes']}")
    if region not in lic["regions"] and "ANY" not in lic["regions"]:
        reasons.append(f"地域 '{region}' 不在许可范围 {lic['regions']}")
    if RESOLUTION_TIERS[resolution] > RESOLUTION_TIERS[lic["max_resolution"]]:
        reasons.append(f"清晰度 '{resolution}' 超过许可上限 '{lic['max_resolution']}'")
    expires = lic.get("expires_on")
    if expires and as_of > expires:
        reasons.append(f"许可已于 {expires} 到期（评估日 {as_of}）")
    return reasons
