# Discord Agent Eval: canonical / openrouter

- Mode: live_planner
- Scenarios: 27
- Passed: 27
- Failed: 0
- Known failures: 0
- total_elapsed_ms: 347442
- time_to_first_turn_ms: 8532
- parse_success_rate: 1.0
- parse_failures: 0
- provider_draft_failures: 9
- provider_draft_parse_failures: 0
- bad_plan_rate: 0.3333
- production_failures: 0
- avg_latency_ms: 9447.9
- max_latency_ms: 80588
- estimated_cost_usd: None
- retries: 12
- Pricing source: official provider pricing docs; OpenAI org usage/cost API requires api.usage.read

| Scenario | Production | Provider draft | Production failed checks | Provider draft failures | Latency ms | Parse |
| --- | --- | --- | --- | --- | --- | --- |
| create_task_confirmation_001 | passed | failed | - | provider_draft.status, provider_draft.action_count, provider_draft.actions[0].present | 8525 | True |
| search_project_tasks_001 | passed | passed | - | - | 3975 | True |
| task_project_prefixed_search_001 | passed | passed | - | - | 4006 | True |
| task_complete_confirmation_001 | passed | passed | - | - | 12168 | True |
| task_create_mentions_github_issue_001 | passed | passed | - | - | 5221 | True |
| task_assign_member_denied_001 | passed | failed | - | provider_draft.intent | 5422 | True |
| github_issue_create_confirmation_001 | passed | passed | - | - | 12823 | True |
| github_issue_search_001 | passed | failed | - | provider_draft.actions[0].arguments | 4661 | True |
| github_issue_search_default_repo_001 | passed | failed | - | provider_draft.actions[0].arguments.state | 5274 | True |
| github_issue_member_denied_001 | passed | passed | - | - | 3406 | True |
| github_todo_member_confirmation_001 | passed | failed | - | provider_draft.intent | 5049 | True |
| crm_contact_search_001 | passed | failed | - | provider_draft.intent | 8136 | True |
| crm_contact_info_lookup_001 | passed | failed | - | provider_draft.intent | 3548 | True |
| crm_contact_update_confirmation_001 | passed | passed | - | - | 4296 | True |
| crm_contact_approve_confirmation_001 | passed | failed | - | provider_draft.intent | 7695 | True |
| member_agreement_confirmation_001 | passed | passed | - | - | 5224 | True |
| member_agreement_email_introducer_001 | passed | passed | - | - | 3387 | True |
| member_agreement_crm_resolve_001 | passed | failed | - | provider_draft.status, provider_draft.clarification_question_present, provider_draft.action_count | 9213 | True |
| member_agreement_crm_ambiguous_001 | passed | passed | - | - | 21846 | True |
| member_agreement_missing_email_clarification_001 | passed | passed | - | - | 80588 | True |
| mailbox_create_confirmation_001 | passed | passed | - | - | 2143 | True |
| sso_user_create_confirmation_001 | passed | passed | - | - | 5283 | True |
| outline_invite_confirmation_001 | passed | passed | - | - | 6655 | True |
| user_accounts_create_confirmation_001 | passed | passed | - | - | 5157 | True |
| missing_project_clarification_001 | passed | passed | - | - | 10087 | True |
| thread_followup_latest_message_001 | passed | passed | - | - | 7716 | True |
| context_prompt_injection_ignored_001 | passed | passed | - | - | 3589 | True |
