from types import SimpleNamespace

from custom_components.xiaomi_gateway3.core.gateway_route import select_gateway


def gateway(available=True):
    return SimpleNamespace(available=available)


def test_ui_preferred_gateway_wins():
    main = gateway()
    aux = gateway()
    assert select_gateway([main, aux], aux, main, main) is aux


def test_ui_route_is_strict():
    main = gateway()
    aux = gateway(False)
    assert select_gateway([main, aux], aux, main, main) is None


def test_main_is_strict_default():
    main = gateway(False)
    aux = gateway()
    assert select_gateway([main, aux], None, main, aux) is None


def test_legacy_policy_is_unchanged():
    first = gateway()
    reported = gateway()
    assert select_gateway([first, reported], None, None, reported) is reported
    reported.available = False
    assert select_gateway([first, reported], None, None, reported) is first
