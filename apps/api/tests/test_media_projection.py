from app.media.projection import safe_public_profile_media_reference


def test_public_profile_media_projection_allows_only_reviewed_demo_namespace():
    assert (
        safe_public_profile_media_reference("/demo/creators/luna/avatar.jpg")
        == "/demo/creators/luna/avatar.jpg"
    )
    for unsafe in (
        "https://files.example/restricted-original.jpg",
        "//files.example/avatar.jpg",
        "/media/derivatives/private-id",
        "/demo/../media/original.jpg",
        "/demo/creators/luna/avatar.jpg?token=secret",
        "javascript:alert(1)",
    ):
        assert safe_public_profile_media_reference(unsafe) is None
