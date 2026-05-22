from __future__ import annotations

from pathlib import Path

from main_computer.viewport import APPLICATIONS_INDEX_HTML
from main_computer.website_project_manifest import (
    create_website_project,
    read_website_project_files,
    save_website_project_files,
)


ROOT = Path(__file__).resolve().parents[1]


def test_applications_shell_loads_real_grapesjs_assets_for_website_builder() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")

    assert "https://unpkg.com/grapesjs/dist/css/grapes.min.css" in html
    assert "https://unpkg.com/grapesjs" in html
    assert "grapesjs@0.22.15" not in html
    assert "website-builder-grapes" in APPLICATIONS_INDEX_HTML
    assert "window.grapesjs.init" in APPLICATIONS_INDEX_HTML
    assert "configureWebsiteBuilderGrapesBlocks" in APPLICATIONS_INDEX_HTML


def test_website_builder_has_visual_canvas_assets_and_script_source_file() -> None:
    app = (ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")
    bindings = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "websites.js").read_text(encoding="utf-8")
    css = (ROOT / "main_computer" / "web" / "applications" / "styles" / "website-builder.css").read_text(encoding="utf-8")

    assert 'id="website-builder-grapes"' in app
    assert 'id="website-builder-grapes-fallback"' in app
    assert 'data-website-builder-file="js"' in app
    assert 'id="website-builder-js"' in app
    assert "script.js" in app
    assert "websiteBuilderDefaultAssetSvgs" in script
    assert "websiteBuilderHeroBlock" in script
    assert "websiteBuilderFeaturesBlock" in script
    assert "websiteBuilderBlogListBlock" in script
    assert 'blocks.add("mc-blog-list"' in script
    assert "websiteBuilderBlogPostViewerBlock" in script
    assert 'blocks.add("mc-blog-post-viewer"' in script
    assert 'data-mc-widget="blog-post-viewer"' in script
    assert 'data-source-ref="blog.posts"' in script
    assert "/api/site/blog/posts" in script
    assert "websiteBuilderEnsureBlogWidgetAssets" in script
    assert "editor.BlockManager" in script
    assert "assetManager: {assets: websiteBuilderDefaultAssets()}" in script
    assert "js: websiteBuilderJs?.value" in script
    assert "websiteBuilderGrapesCanvas" in bindings
    assert ".website-builder-grapes-host" in css
    assert ".website-builder-grapes-fallback[hidden]" in css
    assert "#website-builder-grapes-fallback[hidden]" in css
    assert "display: none !important" in css
    assert "pointer-events: none !important" in css


def test_website_project_files_include_script_js_for_baked_output(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "visual-site", "Visual Site", kind="landing-page")
    payload = read_website_project_files(tmp_path, project.id)

    assert payload["site"]["content"]["index_html"] is True
    assert payload["site"]["content"]["style_css"] is True
    assert payload["site"]["content"]["script_js"] is True
    assert payload["site"]["content"]["builder_json"] is True
    assert '<script src="/script.js" defer></script>' in payload["html"]
    assert "Visual canvas ready" in payload["html"]
    assert "Main Computer website loaded" in payload["js"]
    assert "mcBlogWidgetSelector" in payload["js"]
    assert "mcBlogPostViewerSelector" in payload["js"]
    assert "mcBlogWidgetApplyGeneratedPageMode" in payload["js"]
    assert "mcBlogWidgetSanitizeRichHtml" in payload["js"]
    assert "/api/site/blog/posts" in payload["js"]
    assert "/api/site/blog/posts/" in payload["js"]
    assert "Main Computer blog widget styles" in payload["css"]
    assert "mc-blog-article-presentation-v1" in payload["css"]
    assert 'body[data-mc-blog-route-mode="index"] .mc-blog-post-widget' in payload["css"]
    assert '"engine": "grapesjs"' in payload["builder"]

    save_website_project_files(
        tmp_path,
        project.id,
        html="<main><h1>Edited</h1></main>",
        css="body { color: rebeccapurple; }\n",
        js="window.editedSite = true;\n",
        builder='{"engine": "grapesjs", "script": "script.js"}\n',
    )
    updated = read_website_project_files(tmp_path, project.id)
    assert "<h1>Edited</h1>" in updated["html"]
    assert "rebeccapurple" in updated["css"]
    assert "window.editedSite" in updated["js"]
    assert '"script": "script.js"' in updated["builder"]


def test_website_project_save_injects_blog_widget_assets_for_existing_sites(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "blog-widget-site", "Blog Widget Site", kind="landing-page")
    save_website_project_files(
        tmp_path,
        project.id,
        html="""<main><section data-mc-widget="blog-list" data-source-ref="blog.posts" data-limit="2"><div data-mc-blog-posts></div></section></main>""",
        css="body { color: rebeccapurple; }\n",
        js="window.oldSiteScript = true;\n",
        builder='{"engine": "grapesjs", "script": "script.js"}\n',
    )

    updated = read_website_project_files(tmp_path, project.id)
    assert "window.oldSiteScript = true;" in updated["js"]
    assert "mcBlogWidgetSelector" in updated["js"]
    assert "/api/site/blog/posts" in updated["js"]
    assert "body { color: rebeccapurple; }" in updated["css"]
    assert "Main Computer blog widget styles" in updated["css"]
    assert "mc-blog-article-presentation-v1" in updated["css"]
    assert "mc-blog-index-grid-layout-v1" in updated["css"]
    assert '.mc-section.mc-blog-widget[data-mc-widget="blog-list"]' in updated["css"]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in updated["css"]
    assert "mcBlogWidgetSanitizeRichHtml" in updated["js"]

    save_website_project_files(
        tmp_path,
        project.id,
        html=updated["html"],
        css=updated["css"],
        js=updated["js"],
        builder=updated["builder"],
    )
    round_trip = read_website_project_files(tmp_path, project.id)
    assert round_trip["css"].count("Main Computer blog widget styles") == 1
    assert round_trip["js"] == updated["js"]


def test_website_project_save_injects_blog_post_viewer_assets_for_existing_sites(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "blog-viewer-site", "Blog Viewer Site", kind="landing-page")
    save_website_project_files(
        tmp_path,
        project.id,
        html="""<main><section data-mc-widget="blog-post-viewer" data-source-ref="blog.posts" data-route-prefix="/blog/"><div data-mc-blog-post-viewer></div></section></main>""",
        css="body { color: seagreen; }\n",
        js="window.oldPostViewerScript = true;\n",
        builder='{"engine": "grapesjs", "script": "script.js"}\n',
    )

    updated = read_website_project_files(tmp_path, project.id)
    assert "window.oldPostViewerScript = true;" in updated["js"]
    assert "mcBlogPostViewerSelector" in updated["js"]
    assert "/api/site/blog/posts/" in updated["js"]
    assert "body { color: seagreen; }" in updated["css"]
    assert "mc-blog-post-widget__article" in updated["css"]
    assert "mc-blog-index-grid-layout-v1" in updated["css"]
