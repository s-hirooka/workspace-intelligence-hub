from pathlib import Path


TEMPLATE = Path("app/web/templates/index.html")


def test_workspace_selection_is_persisted_across_reload():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "localStorage.getItem(WORKSPACE_STORAGE_KEY)" in html
    assert "localStorage.setItem(WORKSPACE_STORAGE_KEY" in html
    assert "persistWorkspaceKey(workspaceKey())" in html


def test_unconfigured_ga4_clears_previous_workspace_values():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "function clearGa4" in html
    assert "clearGa4(d.notice" in html


def test_sns_button_is_reenabled_before_dashboard_refresh():
    html = TEMPLATE.read_text(encoding="utf-8")
    handler_start = html.index("$('#sns-sync').onclick")
    handler_end = html.index("$('#tiktok-import').onclick", handler_start)
    handler = html[handler_start:handler_end]

    assert handler.index("busy(button,false)") < handler.index("await refreshSns()")


def test_sns_ranking_supports_facebook_and_newest_first():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="sns-ranking-platform"' in html
    assert '<option value="facebook">Facebook</option>' in html
    assert '<option value="published_at">投稿日（新しい順）</option>' in html
    assert "api('/sns/rankings?'" in html


def test_facebook_account_card_is_rendered():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "Facebookアカウント" in html
    assert 'id="fb-page-name"' in html
    assert 'id="fb-page-id"' in html
    assert 'id="fb-post-count"' in html
    assert "api('/sns/facebook/analytics?" in html


def test_google_business_profile_card_is_rendered_and_refreshed_per_workspace():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "Googleビジネスプロフィール" in html
    assert 'id="gbp-search-impressions"' in html
    assert 'id="gbp-maps-impressions"' in html
    assert 'id="gbp-website-clicks"' in html
    assert "api('/sns/google-business-profile?" in html
    assert "refreshGoogleBusinessProfile()" in html


def test_instagram_funnel_and_aggregate_audience_are_rendered():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "Instagram導線・オーディエンス" in html
    assert 'id="ig-funnel-visits"' in html
    assert 'id="ig-funnel-link-taps"' in html
    assert 'id="ig-audience-kind"' in html
    assert 'id="ig-audience-dimension"' in html
    assert 'id="ig-audience-engaged-total"' in html
    assert 'id="ig-audience-reached-total"' in html
    assert "renderInstagramFunnelAudience(d)" in html
    assert '<option value="profile_visits">プロフィール遷移</option>' in html
    assert '<option value="follows">フォロー</option>' in html
