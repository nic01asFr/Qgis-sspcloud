"""Injection CSS/JS noVNC pour l'embed desk."""

from hub.main import _inject_vnc_desk_embed

_VNC_LITE_SNIPPET = """<!DOCTYPE html>
<html><head><title>noVNC</title></head>
<body>
<script>
rfb.scaleViewport = readQueryVariable('scale', false);
</script>
</body></html>
"""


def test_inject_vnc_desk_embed_masque_top_bar():
    out = _inject_vnc_desk_embed(_VNC_LITE_SNIPPET)
    assert "qgis-desk-embed" in out
    assert "#top_bar" in out
    assert "clipViewport = false" in out
    assert "readQueryVariable('scale', false)" not in out


def test_inject_vnc_desk_embed_idempotent():
    once = _inject_vnc_desk_embed(_VNC_LITE_SNIPPET)
    twice = _inject_vnc_desk_embed(once)
    assert twice.count("qgis-desk-embed") == 1
