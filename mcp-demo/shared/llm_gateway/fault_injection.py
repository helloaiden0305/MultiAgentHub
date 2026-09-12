"""仅供本地服务台验证的受控故障注入器。

规则仅驻留在 LLM Service 进程内存，消耗后自动失效，不会写入控制面数据库。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


VALID_FAULT_MODES = {"connection_failure", "abort_after_first_delta"}


@dataclass
class FaultInjectionRule:
    endpoint_id: str
    mode: str
    remaining: int


class LocalFaultInjector:
    """按 Endpoint 消耗一次性本地故障规则。"""

    def __init__(self) -> None:
        self._rules: dict[tuple[str, str], FaultInjectionRule] = {}

    def configure(self, endpoint_id: str, mode: str, times: int) -> dict:
        if mode not in VALID_FAULT_MODES:
            raise ValueError("不支持的故障类型")
        if not endpoint_id or not 1 <= times <= 5:
            raise ValueError("故障次数必须为 1 到 5")
        rule = FaultInjectionRule(endpoint_id=endpoint_id, mode=mode, remaining=times)
        self._rules[(endpoint_id, mode)] = rule
        return asdict(rule)

    def consume(self, endpoint_id: str, mode: str) -> bool:
        key = (endpoint_id, mode)
        rule = self._rules.get(key)
        if rule is None or rule.remaining <= 0:
            return False
        rule.remaining -= 1
        if rule.remaining == 0:
            self._rules.pop(key, None)
        return True

    def clear(self) -> None:
        self._rules.clear()

    def snapshot(self) -> list[dict]:
        return [asdict(rule) for rule in self._rules.values()]
