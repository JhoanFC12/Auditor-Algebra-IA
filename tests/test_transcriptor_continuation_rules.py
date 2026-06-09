from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.domain.continuation_rules import should_absorb_direct_item_into_pending


def test_should_absorb_direct_item_into_pending_is_strict_with_newer_numbers():
    assert should_absorb_direct_item_into_pending(pending_num=7, incoming_num=7, options_only_like=False) is True
    assert should_absorb_direct_item_into_pending(pending_num=7, incoming_num=11, options_only_like=True) is False
    assert should_absorb_direct_item_into_pending(pending_num=53, incoming_num=57, options_only_like=True) is False
    assert should_absorb_direct_item_into_pending(pending_num=7, incoming_num=0, options_only_like=True) is True
