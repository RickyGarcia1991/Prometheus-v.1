from prometheus_assistant.linux_power import read_power


def supply(root, name, **attributes):
    folder = root/name
    folder.mkdir()
    for key, value in attributes.items():
        (folder/key).write_text(str(value), encoding='ascii')


def test_surface_style_full_battery_and_external_power(tmp_path):
    supply(tmp_path, 'BAT1', type='Battery', capacity=100, status='Not charging')
    supply(tmp_path, 'ADP1', type='Mains', online=1)
    assert read_power(tmp_path) == (100, True)


def test_battery_level_does_not_imply_external_power(tmp_path):
    supply(tmp_path, 'BAT1', type='Battery', capacity=100, status='Full')
    assert read_power(tmp_path) == (100, None)
    supply(tmp_path, 'AC', type='Mains', online=0)
    assert read_power(tmp_path) == (100, False)


def test_peripheral_battery_and_unknown_supply_are_not_host_power(tmp_path):
    supply(tmp_path, 'mouse', type='Battery', capacity=88, scope='Device')
    supply(tmp_path, 'AC', type='Mains', online='unknown')
    assert read_power(tmp_path) == (None, None)


def test_multiple_batteries_do_not_create_an_unweighted_average(tmp_path):
    supply(tmp_path, 'BAT0', type='Battery', capacity=20, present=1)
    supply(tmp_path, 'BAT1', type='Battery', capacity=90, present=1)
    assert read_power(tmp_path)[0] is None
    (tmp_path/'BAT1/present').write_text('0')
    assert read_power(tmp_path)[0] == 20


def test_invalid_capacity_and_partial_supply_remain_unknown(tmp_path):
    supply(tmp_path, 'BAT0', type='Battery', capacity=255)
    supply(tmp_path, 'AC0', type='Mains', online=0)
    supply(tmp_path, 'AC1', type='USB', online=2)
    assert read_power(tmp_path) == (None, None)
    (tmp_path/'AC0/online').write_text('1')
    assert read_power(tmp_path) == (None, True)


def test_missing_or_unbounded_inventory_stays_unknown(tmp_path):
    assert read_power(tmp_path/'missing') == (None, None)
    for i in range(33): supply(tmp_path, str(i), type='Mains', online=1)
    assert read_power(tmp_path) == (None, None)
