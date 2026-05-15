from __future__ import annotations

from dataclasses import dataclass


class ConfigError(ValueError):
    """控制面配置错误。"""


_PROFILE_POLICY: dict[str, bool] = {
    "US": True,
    "CN_A": True,
    "HK": True,
    "CRYPTO": False,
}


@dataclass(frozen=True)
class ProfileResult:
    ok: bool
    name: str
    reason: str | None


def is_profile_approved(profile: str) -> bool:
    return _PROFILE_POLICY.get(profile, False)


def require_profile(profile: str) -> ProfileResult:
    # 这里是市场配置硬边界：未批准配置必须阻断，不能静默 fallback 到 US/CN_A。
    if profile not in _PROFILE_POLICY:
        return ProfileResult(ok=False, name=profile, reason=f"unknown profile: {profile}")
    if not is_profile_approved(profile):
        return ProfileResult(ok=False, name=profile, reason=f"profile is not approved: {profile}")
    return ProfileResult(ok=True, name=profile, reason=None)
