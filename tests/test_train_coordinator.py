"""Tests for train status parser."""

from custom_components.singapore.train_coordinator import _parse_train_status


def test_parse_train_status_planned():
    html = """
    <html><body>
      <div>North-South Line planned disruption due to engineering works.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "planned"


def test_parse_train_status_disruption():
    html = """
    <html><body>
      <div>Circle Line service disruption due to track fault.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "disruption"


def test_parse_train_status_normal():
    html = """
    <html><body>
      <div>All train lines are operating normally.</div>
    </body></html>
    """
    data = _parse_train_status(html)
    assert data.status == "normal"
