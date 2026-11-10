"""
MinerU 多 Token 轮换：按顺序使用，当天额度快用完时切到下一个

- 每个 Token 每天有高优先级页数额度（默认 1000 页，北京时间自然日重置），本地按提交页数累计。
- 当前 Token 剩余额度（扣除预留）装不下下一批时切换；接口返回额度/认证类错误时立即标记当天用尽并切换。
- 全部用尽后：queue 继续轮流提交（MinerU 端排队，速度变慢）；stop 抛出异常停止。
- 用量文件只保存 Token 指纹（SHA-256 前 12 位），不落明文。单进程使用。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))


class TokenPoolExhaustedError(RuntimeError):
    """
    全部 Token 当天额度已用尽（when_exhausted=stop）
    """


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


@dataclass
class TokenChoice:
    """
    本次选用的 Token 及其可用页数（over_quota=True 表示已超额，提交后会排队）
    """

    token: str
    capacity: int
    over_quota: bool = False

    @property
    def fp(self) -> str:
        return fingerprint(self.token)


class MinerUTokenPool:
    """
    MinerU Token 池
    """

    def __init__(
        self,
        tokens: list[str],
        usage_file: Path,
        daily_pages: int = 1000,
        reserve_pages: int = 20,
        when_exhausted: str = 'queue',
    ) -> None:
        self.tokens = list(dict.fromkeys(t for t in tokens if t))
        if not self.tokens:
            raise ValueError('MinerU Token 列表为空')
        self.usage_file = usage_file
        self.daily_pages = daily_pages
        self.reserve_pages = reserve_pages
        self.when_exhausted = when_exhausted
        self._queue_cursor = 0

    @staticmethod
    def today() -> str:
        return datetime.now(BEIJING).date().isoformat()

    def _load(self) -> dict:
        if self.usage_file.is_file():
            data = json.loads(self.usage_file.read_text(encoding='utf-8'))
            if data.get('date') == self.today():
                return data
        return {'date': self.today(), 'pages': {}, 'exhausted': []}

    def _save(self, data: dict) -> None:
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        self.usage_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    def remaining(self, token: str) -> int:
        """
        当天剩余可用页数（已扣除预留）；被标记用尽的为 0
        """
        data = self._load()
        fp = fingerprint(token)
        if fp in data['exhausted']:
            return 0
        return max(0, self.daily_pages - self.reserve_pages - data['pages'].get(fp, 0))

    def choose(self, pages_needed: int) -> TokenChoice:
        """
        选 Token：按顺序第一个剩余额度 ≥ 所需页数的；都装不下时取剩余最多的（>0）；全部为 0 按策略处理

        :param pages_needed: 下一批（至少一个文件）的页数
        :return: 选中的 Token 及可用页数
        """
        remains = [(t, self.remaining(t)) for t in self.tokens]
        for token, left in remains:
            if left >= pages_needed:
                return TokenChoice(token, left)
        token, left = max(remains, key=lambda x: x[1])
        if left > 0:
            return TokenChoice(token, left)
        if self.when_exhausted == 'stop':
            raise TokenPoolExhaustedError(f'{len(self.tokens)} 个 MinerU Token 今日额度均已用尽（{self.today()}）')
        token = self.tokens[self._queue_cursor % len(self.tokens)]
        self._queue_cursor += 1
        return TokenChoice(token, pages_needed, over_quota=True)

    def record(self, token: str, pages: int) -> None:
        """
        记录已提交页数
        """
        data = self._load()
        fp = fingerprint(token)
        data['pages'][fp] = data['pages'].get(fp, 0) + pages
        self._save(data)

    def mark_exhausted(self, token: str) -> None:
        """
        接口提示额度用尽或认证失败：当天不再使用
        """
        data = self._load()
        fp = fingerprint(token)
        if fp not in data['exhausted']:
            data['exhausted'].append(fp)
        self._save(data)

    def status(self) -> list[dict]:
        """
        当天各 Token 用量（只含指纹）
        """
        data = self._load()
        return [
            {
                'token': f'{t[:5]}…{fingerprint(t)}',
                'used_pages': data['pages'].get(fingerprint(t), 0),
                'remaining': self.remaining(t),
                'exhausted': fingerprint(t) in data['exhausted'],
            }
            for t in self.tokens
        ]
