def test_ui_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Circular Register" in resp.text
    # sanity-check the JS references the actual API paths it depends on
    assert "/review/queue" in resp.text
    assert "/documents" in resp.text
