#!/usr/bin/env python3
"""Tests for field.py's CrUX response parsing.

The parser was written before a live CrUX key existed, which is exactly the
situation that produces a shape bug that looks like "no data" instead of an
error. These fixtures are built from the response body documented at
https://developer.chrome.com/docs/crux/api so the parser is checked against the
real contract rather than against the author's memory of it.

Run: python3 test_field.py
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request

import field

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def patch_response(payload):
    def fake_urlopen(req, timeout=0):
        return FakeResponse(json.dumps(payload).encode())
    urllib.request.urlopen = fake_urlopen


def patch_http_error(code):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(
            "u", code, "err", {}, io.BytesIO(b'{"error":{"message":"x"}}'))
    urllib.request.urlopen = fake_urlopen


# Response body per the CrUX API reference: record.metrics.<name> carries
# histogram + percentiles.p75 for timing metrics, and fractions for
# categorization metrics.
CRUX_OK = {
    "record": {
        "key": {"formFactor": "PHONE", "origin": "https://example.com"},
        "metrics": {
            "largest_contentful_paint": {
                "histogram": [
                    {"start": 0, "end": 2500, "density": 0.62},
                    {"start": 2500, "end": 4000, "density": 0.25},
                    {"start": 4000, "density": 0.13},
                ],
                "percentiles": {"p75": 3800},
            },
            "interaction_to_next_paint": {
                "histogram": [{"start": 0, "end": 200, "density": 0.8}],
                "percentiles": {"p75": 240},
            },
            "cumulative_layout_shift": {
                "histogram": [{"start": "0.00", "end": "0.10", "density": 0.9}],
                "percentiles": {"p75": "0.05"},
            },
            "experimental_time_to_first_byte": {
                "histogram": [{"start": 0, "end": 800, "density": 0.7}],
                "percentiles": {"p75": 700},
            },
            "largest_contentful_paint_image_time_to_first_byte": {
                "percentiles": {"p75": 700}},
            "largest_contentful_paint_image_resource_load_delay": {
                "percentiles": {"p75": 400}},
            "largest_contentful_paint_image_resource_load_duration": {
                "percentiles": {"p75": 1500}},
            "largest_contentful_paint_image_element_render_delay": {
                "percentiles": {"p75": 1200}},
            "largest_contentful_paint_resource_type": {
                "fractions": {"image": 0.9, "text": 0.1}},
        },
        "collectionPeriod": {
            "firstDate": {"year": 2026, "month": 7, "day": 12},
            "lastDate": {"year": 2026, "month": 8, "day": 8},
        },
    }
}


def test_ok():
    print("query returns parsed metrics on a documented response")
    patch_response(CRUX_OK)
    r = field.crux_query("KEY", "PHONE", origin="https://example.com")
    check("status ok", r["status"] == "ok", r.get("status"))
    m = r["metrics"]
    check("LCP p75 read from percentiles.p75", m["largest_contentful_paint"]["p75"] == 3800)
    check("LCP rated needs-improvement at 3800ms",
          m["largest_contentful_paint"]["rating"] == "needs-improvement",
          m["largest_contentful_paint"].get("rating"))
    check("INP rated needs-improvement at 240ms",
          m["interaction_to_next_paint"]["rating"] == "needs-improvement",
          m["interaction_to_next_paint"].get("rating"))
    # CrUX returns CLS as a STRING. A float() that is not applied makes a
    # string compare against a float threshold, which raises or misrates.
    check("CLS string '0.05' rated good",
          m["cumulative_layout_shift"]["rating"] == "good",
          m["cumulative_layout_shift"].get("rating"))
    check("TTFB rated good at 700ms",
          m["experimental_time_to_first_byte"]["rating"] == "good")
    check("labels attached", m["largest_contentful_paint"]["label"] == "LCP")
    check("all four LCP subparts captured", len(r["lcp_subparts"]) == 4, r["lcp_subparts"])
    check("subpart value read", r["lcp_subparts"][
        "largest_contentful_paint_image_element_render_delay"] == 1200)
    check("fractions metric captured as lcp_resource_type",
          (r.get("lcp_resource_type") or {}).get("image") == 0.9, r.get("lcp_resource_type"))
    check("collectionPeriod carried", r["collection_period"]["lastDate"]["day"] == 8)


def test_404_is_not_a_pass():
    print("404 renders as insufficient-data, never as good")
    patch_http_error(404)
    r = field.crux_query("KEY", "PHONE", origin="https://nodata.example")
    check("status insufficient-data", r["status"] == "insufficient-data", r.get("status"))
    check("no metrics fabricated", "metrics" not in r or not r.get("metrics"))
    check("says it is not a pass", "Not a pass" in r.get("detail", ""))


def test_403_and_429():
    print("auth and quota errors are distinguished")
    patch_http_error(403)
    check("403 -> forbidden",
          field.crux_query("K", "PHONE", origin="https://e.com")["status"] == "forbidden")
    patch_http_error(429)
    check("429 -> rate-limited",
          field.crux_query("K", "PHONE", origin="https://e.com")["status"] == "rate-limited")


def test_url_vs_origin():
    print("url and origin are mutually exclusive in the request body")
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(json.dumps(CRUX_OK).encode())
    urllib.request.urlopen = fake_urlopen

    field.crux_query("K", "PHONE", origin="https://e.com")
    check("origin request sends origin only",
          "origin" in captured["body"] and "url" not in captured["body"], captured["body"].keys())
    field.crux_query("K", "PHONE", url="https://e.com/p")
    check("url request sends url only",
          "url" in captured["body"] and "origin" not in captured["body"], captured["body"].keys())
    check("form factor passed through", captured["body"]["formFactor"] == "PHONE")
    check("LCP subparts requested",
          "largest_contentful_paint_image_element_render_delay" in captured["body"]["metrics"])


def test_rate_thresholds():
    print("thresholds match Google's published boundaries")
    cases = [("LCP", 2500, "good"), ("LCP", 2501, "needs-improvement"), ("LCP", 4001, "poor"),
             ("INP", 200, "good"), ("INP", 501, "poor"),
             ("CLS", 0.1, "good"), ("CLS", 0.26, "poor"),
             ("TTFB", 800, "good"), ("TTFB", 1801, "poor")]
    for label, val, want in cases:
        got = field.rate_label(label, val) if hasattr(field, "rate_label") else field.rate(
            {v[3]: k for k, v in field.THRESHOLDS.items()}[label], val)
        check(f"{label} {val} -> {want}", got == want, f"got {got}")


if __name__ == "__main__":
    for fn in (test_ok, test_404_is_not_a_pass, test_403_and_429,
               test_url_vs_origin, test_rate_thresholds):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("all field.py parser tests pass")
