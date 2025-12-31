"""
Payment Sync V2 - data flow: UI "Merge with AI"

This is intentionally in a runnable "commands" format:
    python commands/payment_sync_v2_merge_with_ai_flow.py

It prints a hierarchical call graph + example inputs/outputs for each hop.
"""

from __future__ import annotations

import json


def build_flow():
    return {
        "entrypoint": {
            "ui": {
                "file": "templates/payment_sync/sync_v2.html",
                "action": "Click button: Merge with AI",
                "function": "mergeWithAI()",
                "example_input": {
                    "ui_state": {
                        "selected_bank_payments": ["bank-001", "bank-009"],
                        "ai_model": "openai/gpt-4o",
                        "ai_prompt": "Match bank payment selections to DB payments; prioritize tenant/apartment keywords.",
                        "ai_custom_prompt": "",
                        "amount_delta": 100,
                        "db_days_before": 30,
                        "db_days_after": 30,
                        "with_confirmed": False,
                    }
                },
                "calls": [
                    {
                        "function": "mergeSelectionMatch(mode, aiCustomPrompt)",
                        "called_by": "mergeWithAI()",
                        "example_input": {
                            "mode": "ai",
                            "aiCustomPrompt": "",
                        },
                        "does": [
                            "Validates at least 1 bank payment is selected",
                            "Computes selectionKey from selected bank payments",
                            "Builds payload and POSTs to MATCH_SELECTION_URL",
                        ],
                        "calls": [
                            {
                                "function": "computeSelectionKeyFromSelectedBankPayments()",
                                "example_output": "bank-001+bank-009",
                            },
                            {
                                "function": "getSelectedFilePaymentsForPayload()",
                                "example_output": [
                                    {
                                        "id": "bank-001",
                                        "amount": 3300.0,
                                        "payment_date": "2025-12-01",
                                        "notes": "Wire payment from Michael Steinhardt for apt. 630-205",
                                        "apartment_name": "630-205",
                                        "payment_method_name": "Wire",
                                        "bank_name": "Bank of America",
                                        "payment_type_type": "In",
                                    },
                                    {
                                        "id": "bank-009",
                                        "amount": 330.0,
                                        "payment_date": "2025-11-30",
                                        "notes": "Zelle - partial payment 630-205",
                                        "apartment_name": "630-205",
                                        "payment_method_name": "Zelle",
                                        "bank_name": "Bank of America",
                                        "payment_type_type": "In",
                                    },
                                ],
                            },
                            {
                                "http": {
                                    "method": "POST",
                                    "url_name": "match_selection_v2",
                                    "url_pattern": "/payments-sync-v2/match-selection/",
                                    "js_constant": "MATCH_SELECTION_URL",
                                    "headers": {
                                        "Content-Type": "application/json",
                                        "X-CSRFToken": "<csrf token>",
                                    },
                                    "body_example": {
                                        "mode": "ai",
                                        "selected_file_ids": ["bank-001", "bank-009"],
                                        "selected_file_payments": "<array from getSelectedFilePaymentsForPayload()>",
                                        "db_days_before": "30",
                                        "db_days_after": "30",
                                        "with_confirmed": False,
                                        "amount_delta": "100",
                                        "date_delta": "4",
                                        "ai_model": "openai/gpt-4o",
                                        "ai_base_prompt": "Match bank payment selections to DB payments; prioritize tenant/apartment keywords.",
                                        "ai_custom_prompt": "",
                                    },
                                }
                            },
                        ],
                    }
                ],
            }
        },
        "backend": {
            "file": "mysite/views/payment_sync_v2.py",
            "endpoint": "match_selection_v2(request)",
            "decorator": "@user_has_role('Admin')",
            "example_input": {
                "request.method": "POST",
                "request.body": "<the JSON body above>",
            },
            "calls": [
                {
                    "function": "_build_composite_from_selected_file_payments(selected_file_payments)",
                    "example_input": "<selected_file_payments array>",
                    "example_output": {
                        "amount_total": 3630.0,
                        "date_from": "2025-11-30",
                        "date_to": "2025-12-01",
                        "apartment_name": "630-205",
                        "apartment_candidates": [],
                        "payment_method_name": "",
                        "payment_method_candidates": ["Wire", "Zelle"],
                        "bank_name": "Bank of America",
                        "bank_candidates": [],
                        "notes_combined": "Wire payment from Michael Steinhardt for apt. 630-205 | Zelle - partial payment 630-205",
                        "direction": "In",
                        "selected_count": 2,
                    },
                },
                {
                    "function": "Payment.objects.filter(payment_date__range=(db_from, db_to))",
                    "does": [
                        "DB window is composite date range ±30 days",
                        "If with_confirmed is false, filters payment_status='Pending'",
                        "Select related: payment_type, payment_method, apartment, booking__tenant, bank",
                    ],
                    "example_input": {"db_from": "2025-10-31", "db_to": "2025-12-31", "with_confirmed": False},
                    "example_output": {"db_qs_count": 218},
                },
                {
                    "function": "_ai_prefilter_top100(db_payments_qs, composite, amount_delta)",
                    "example_input": {"amount_delta": 100, "selected_count": 2, "amount_total": 3630.0},
                    "example_output": {
                        "count": 100,
                        "notes": "Heuristic prefilter by direction/type, amount window, date window, token overlap",
                    },
                },
                {
                    "function": "_ai_match_with_openrouter(model, base_prompt, custom_prompt, composite, candidate_payments)",
                    "example_input": {
                        "model": "openai/gpt-4o",
                        "base_prompt_len": 86,
                        "custom_prompt_len": 0,
                        "candidate_payments_count": 100,
                        "composite_desc": "<minified composite JSON passed to model>",
                    },
                    "example_output_success": [
                        {
                            "db_id": 5521,
                            "score": 88,
                            "match_type": "ai",
                            "criteria": "Same apartment 630-205, within date range, amount close to combined deposits, notes mention partial payment.",
                        },
                        {
                            "db_id": 5530,
                            "score": 51,
                            "match_type": "ai",
                            "criteria": "Amount matches but weaker keyword overlap; consider if this is a split payment.",
                        },
                    ],
                    "example_output_error": {"error": "AI did not return a JSON array"},
                },
                {
                    "function": "_payment_to_rich_dict(p)",
                    "does": "Turns Payment model into JSON-friendly dict used by UI",
                    "example_output": {
                        "id": 5521,
                        "amount": "3630.00",
                        "payment_date": "2025-12-01",
                        "payment_type": 1,
                        "payment_type_name": "Rent (In)",
                        "payment_method_name": "Wire",
                        "bank_name": "Bank of America",
                        "apartment_name": "630-205",
                        "tenant_name": "Michael Steinhardt",
                        "payment_status": "Pending",
                        "notes": "Wire received - Dec rent",
                        "keywords": "rent,december,630-205",
                    },
                },
                {
                    "function": "return JsonResponse(payload)",
                    "example_output": {
                        "selected_key": "bank-001+bank-009",
                        "matched_payments": [
                            {
                                "type": "ai",
                                "db_payment": "<rich db payment dict>",
                                "score": 88.0,
                                "match_type": "ai",
                                "criteria": "Same apartment 630-205, within date range, amount close to combined deposits...",
                            }
                        ],
                    },
                },
            ],
        },
        "ui_after_response": {
            "file": "templates/payment_sync/sync_v2.html",
            "function": "mergeSelectionMatch(mode, aiCustomPrompt) .then(data => ...)",
            "does": [
                "Caches matched candidates by selectionKey + source ('ai')",
                "Extracts db_payment dicts from candidates and merges them into the local db list",
                "Sets db_filter_mode = 'ai' and re-renders DB list filtered to AI candidates",
            ],
            "calls": [
                {"function": "setCachedMatch(selectionKey, 'ai', aiCandidatesOnly)"},
                {"function": "mergeDBPayments(dbFromCandidates)"},
                {"function": "normalizeDBPaymentsForUI()"},
                {"function": "ensureNewPaymentPlaceholder()"},
                {"function": "renderDBPayments()"},
            ],
            "important_note": {
                "what": "This does NOT persist a merge to the backend",
                "why": "The bottom button 'Merge Selection' only updates client-side JSON and has a TODO to send to backend.",
            },
        },
    }


def main():
    flow = build_flow()
    print(json.dumps(flow, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


