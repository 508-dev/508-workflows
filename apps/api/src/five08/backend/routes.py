"""Route registration for the backend FastAPI app."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


RouteHandler = Callable[..., Any]


class BackendRouteSurface(Protocol):
    """Route-facing API module contract used by this registrar."""

    _OptionalDirectoryStaticFiles: type[StaticFiles]
    dashboard_assets_dir: Callable[[], Path]
    agent_confirmation_handler: RouteHandler
    agent_request_handler: RouteHandler
    audit_event_handler: RouteHandler
    auth_callback_handler: RouteHandler
    auth_discord_link_consume_handler: RouteHandler
    auth_discord_link_create_handler: RouteHandler
    auth_discord_link_redirect_handler: RouteHandler
    auth_login_handler: RouteHandler
    auth_logout_handler: RouteHandler
    auth_me_handler: RouteHandler
    dashboard_add_gig_application_handler: RouteHandler
    dashboard_add_project_historical_member_handler: RouteHandler
    dashboard_add_project_user_handler: RouteHandler
    dashboard_agent_report_handler: RouteHandler
    dashboard_assign_onboarder_handler: RouteHandler
    dashboard_audit_events_handler: RouteHandler
    dashboard_bulk_update_projects_handler: RouteHandler
    dashboard_clear_job_lead_staging_recovery_handler: RouteHandler
    dashboard_configuration_handler: RouteHandler
    dashboard_create_project_handler: RouteHandler
    dashboard_discord_diagnostics_handler: RouteHandler
    dashboard_erpnext_account_managers_handler: RouteHandler
    dashboard_erpnext_contacts_handler: RouteHandler
    dashboard_erpnext_cost_centers_handler: RouteHandler
    dashboard_erpnext_customers_handler: RouteHandler
    dashboard_gig_detail_handler: RouteHandler
    dashboard_gigs_handler: RouteHandler
    dashboard_handler: RouteHandler
    dashboard_job_lead_scrape_status_handler: RouteHandler
    dashboard_job_leads_handler: RouteHandler
    dashboard_post_job_lead_handler: RouteHandler
    dashboard_review_job_lead_handler: RouteHandler
    dashboard_stage_job_lead_handler: RouteHandler
    dashboard_job_detail_handler: RouteHandler
    dashboard_delete_job_channel_handler: RouteHandler
    dashboard_job_channels_handler: RouteHandler
    dashboard_jobs_handler: RouteHandler
    dashboard_me_handler: RouteHandler
    dashboard_newsletter_status_handler: RouteHandler
    dashboard_newsletter_suppressions_handler: RouteHandler
    dashboard_notifications_handler: RouteHandler
    dashboard_onboarding_email_draft_handler: RouteHandler
    dashboard_onboarding_email_send_handler: RouteHandler
    dashboard_onboarding_handler: RouteHandler
    dashboard_onboarding_volunteers_handler: RouteHandler
    dashboard_onboarding_volunteer_handler: RouteHandler
    dashboard_onboarding_suggestions_handler: RouteHandler
    dashboard_people_handler: RouteHandler
    dashboard_project_member_candidates_handler: RouteHandler
    dashboard_project_wiki_matches_handler: RouteHandler
    dashboard_projects_handler: RouteHandler
    dashboard_remove_project_historical_member_handler: RouteHandler
    dashboard_remove_project_user_handler: RouteHandler
    dashboard_rerun_job_handler: RouteHandler
    dashboard_setup_engineer_handler: RouteHandler
    dashboard_sync_newsletters_handler: RouteHandler
    dashboard_sync_people_handler: RouteHandler
    dashboard_sync_projects_handler: RouteHandler
    dashboard_sync_job_leads_handler: RouteHandler
    dashboard_update_configuration_handler: RouteHandler
    dashboard_update_gig_application_status_handler: RouteHandler
    dashboard_update_gig_status_handler: RouteHandler
    dashboard_update_job_channel_handler: RouteHandler
    dashboard_update_onboarding_status_handler: RouteHandler
    dashboard_update_project_status_handler: RouteHandler
    dashboard_update_project_wiki_match_handler: RouteHandler
    docuseal_webhook_handler: RouteHandler
    espocrm_people_sync_webhook_handler: RouteHandler
    espocrm_webhook_handler: RouteHandler
    google_forms_intake_webhook_handler: RouteHandler
    health_handler: RouteHandler
    ingest_handler: RouteHandler
    job_status_handler: RouteHandler
    jobs_handler: RouteHandler
    process_contact_handler: RouteHandler
    rerun_job_handler: RouteHandler
    resume_apply_handler: RouteHandler
    resume_extract_handler: RouteHandler
    sync_people_handler: RouteHandler
    tally_intake_webhook_handler: RouteHandler


def register_routes(app: FastAPI, api: BackendRouteSurface) -> None:
    """Register backend API routes against handlers owned by the api module."""
    _OptionalDirectoryStaticFiles = api._OptionalDirectoryStaticFiles
    agent_confirmation_handler = api.agent_confirmation_handler
    agent_request_handler = api.agent_request_handler
    audit_event_handler = api.audit_event_handler
    auth_callback_handler = api.auth_callback_handler
    auth_discord_link_consume_handler = api.auth_discord_link_consume_handler
    auth_discord_link_create_handler = api.auth_discord_link_create_handler
    auth_discord_link_redirect_handler = api.auth_discord_link_redirect_handler
    auth_login_handler = api.auth_login_handler
    auth_logout_handler = api.auth_logout_handler
    auth_me_handler = api.auth_me_handler
    dashboard_add_gig_application_handler = api.dashboard_add_gig_application_handler
    dashboard_add_project_historical_member_handler = (
        api.dashboard_add_project_historical_member_handler
    )
    dashboard_add_project_user_handler = api.dashboard_add_project_user_handler
    dashboard_agent_report_handler = api.dashboard_agent_report_handler
    dashboard_assets_dir = api.dashboard_assets_dir
    dashboard_assign_onboarder_handler = api.dashboard_assign_onboarder_handler
    dashboard_audit_events_handler = api.dashboard_audit_events_handler
    dashboard_bulk_update_projects_handler = api.dashboard_bulk_update_projects_handler
    dashboard_clear_job_lead_staging_recovery_handler = (
        api.dashboard_clear_job_lead_staging_recovery_handler
    )
    dashboard_configuration_handler = api.dashboard_configuration_handler
    dashboard_create_project_handler = api.dashboard_create_project_handler
    dashboard_discord_diagnostics_handler = api.dashboard_discord_diagnostics_handler
    dashboard_erpnext_account_managers_handler = (
        api.dashboard_erpnext_account_managers_handler
    )
    dashboard_erpnext_contacts_handler = api.dashboard_erpnext_contacts_handler
    dashboard_erpnext_cost_centers_handler = api.dashboard_erpnext_cost_centers_handler
    dashboard_erpnext_customers_handler = api.dashboard_erpnext_customers_handler
    dashboard_gig_detail_handler = api.dashboard_gig_detail_handler
    dashboard_gigs_handler = api.dashboard_gigs_handler
    dashboard_handler = api.dashboard_handler
    dashboard_delete_job_channel_handler = api.dashboard_delete_job_channel_handler
    dashboard_job_lead_scrape_status_handler = (
        api.dashboard_job_lead_scrape_status_handler
    )
    dashboard_job_leads_handler = api.dashboard_job_leads_handler
    dashboard_job_channels_handler = api.dashboard_job_channels_handler
    dashboard_post_job_lead_handler = api.dashboard_post_job_lead_handler
    dashboard_review_job_lead_handler = api.dashboard_review_job_lead_handler
    dashboard_stage_job_lead_handler = api.dashboard_stage_job_lead_handler
    dashboard_job_detail_handler = api.dashboard_job_detail_handler
    dashboard_jobs_handler = api.dashboard_jobs_handler
    dashboard_me_handler = api.dashboard_me_handler
    dashboard_newsletter_status_handler = api.dashboard_newsletter_status_handler
    dashboard_newsletter_suppressions_handler = (
        api.dashboard_newsletter_suppressions_handler
    )
    dashboard_notifications_handler = api.dashboard_notifications_handler
    dashboard_onboarding_email_draft_handler = (
        api.dashboard_onboarding_email_draft_handler
    )
    dashboard_onboarding_email_send_handler = (
        api.dashboard_onboarding_email_send_handler
    )
    dashboard_onboarding_handler = api.dashboard_onboarding_handler
    dashboard_onboarding_volunteers_handler = (
        api.dashboard_onboarding_volunteers_handler
    )
    dashboard_onboarding_volunteer_handler = api.dashboard_onboarding_volunteer_handler
    dashboard_onboarding_suggestions_handler = (
        api.dashboard_onboarding_suggestions_handler
    )
    dashboard_people_handler = api.dashboard_people_handler
    dashboard_project_member_candidates_handler = (
        api.dashboard_project_member_candidates_handler
    )
    dashboard_project_wiki_matches_handler = api.dashboard_project_wiki_matches_handler
    dashboard_projects_handler = api.dashboard_projects_handler
    dashboard_remove_project_historical_member_handler = (
        api.dashboard_remove_project_historical_member_handler
    )
    dashboard_remove_project_user_handler = api.dashboard_remove_project_user_handler
    dashboard_rerun_job_handler = api.dashboard_rerun_job_handler
    dashboard_setup_engineer_handler = api.dashboard_setup_engineer_handler
    dashboard_sync_newsletters_handler = api.dashboard_sync_newsletters_handler
    dashboard_sync_people_handler = api.dashboard_sync_people_handler
    dashboard_sync_projects_handler = api.dashboard_sync_projects_handler
    dashboard_sync_job_leads_handler = api.dashboard_sync_job_leads_handler
    dashboard_update_configuration_handler = api.dashboard_update_configuration_handler
    dashboard_update_gig_application_status_handler = (
        api.dashboard_update_gig_application_status_handler
    )
    dashboard_update_gig_status_handler = api.dashboard_update_gig_status_handler
    dashboard_update_job_channel_handler = api.dashboard_update_job_channel_handler
    dashboard_update_onboarding_status_handler = (
        api.dashboard_update_onboarding_status_handler
    )
    dashboard_update_project_status_handler = (
        api.dashboard_update_project_status_handler
    )
    dashboard_update_project_wiki_match_handler = (
        api.dashboard_update_project_wiki_match_handler
    )
    docuseal_webhook_handler = api.docuseal_webhook_handler
    espocrm_people_sync_webhook_handler = api.espocrm_people_sync_webhook_handler
    espocrm_webhook_handler = api.espocrm_webhook_handler
    google_forms_intake_webhook_handler = api.google_forms_intake_webhook_handler
    health_handler = api.health_handler
    ingest_handler = api.ingest_handler
    job_status_handler = api.job_status_handler
    jobs_handler = api.jobs_handler
    process_contact_handler = api.process_contact_handler
    rerun_job_handler = api.rerun_job_handler
    resume_apply_handler = api.resume_apply_handler
    resume_extract_handler = api.resume_extract_handler
    sync_people_handler = api.sync_people_handler
    tally_intake_webhook_handler = api.tally_intake_webhook_handler

    app.add_api_route("/", health_handler, methods=["GET"])
    app.add_api_route("/health", health_handler, methods=["GET"])

    app.add_api_route(
        "/dashboard",
        dashboard_handler,
        methods=["GET"],
        response_model=None,
    )
    assets_dir = dashboard_assets_dir()
    app.mount(
        "/dashboard/assets",
        _OptionalDirectoryStaticFiles(directory=assets_dir, check_dir=False),
        name="dashboard-assets",
    )
    app.add_api_route(
        "/dashboard/{view}",
        dashboard_handler,
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route("/dashboard/api/me", dashboard_me_handler, methods=["GET"])
    app.add_api_route("/dashboard/api/jobs", dashboard_jobs_handler, methods=["GET"])
    app.add_api_route(
        "/dashboard/api/jobs/{job_id}",
        dashboard_job_detail_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/jobs/{job_id}/rerun",
        dashboard_rerun_job_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/people",
        dashboard_people_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/gigs",
        dashboard_gigs_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/gigs/{engagement_id}",
        dashboard_gig_detail_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads",
        dashboard_job_leads_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads/scrape-status",
        dashboard_job_lead_scrape_status_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/job-channels",
        dashboard_job_channels_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/job-channels/{channel_id}",
        dashboard_update_job_channel_handler,
        methods=["PUT"],
    )
    app.add_api_route(
        "/dashboard/api/job-channels/{channel_id}",
        dashboard_delete_job_channel_handler,
        methods=["DELETE"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads/sync",
        dashboard_sync_job_leads_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads/{lead_id}/review",
        dashboard_review_job_lead_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads/{lead_id}/staging-recovery/clear",
        dashboard_clear_job_lead_staging_recovery_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads/{lead_id}/stage",
        dashboard_stage_job_lead_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gig-leads/{lead_id}/post",
        dashboard_post_job_lead_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/notifications",
        dashboard_notifications_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/projects",
        dashboard_projects_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/project-member-candidates",
        dashboard_project_member_candidates_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/projects/wiki-matches",
        dashboard_project_wiki_matches_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/erpnext/customers",
        dashboard_erpnext_customers_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/erpnext/contacts",
        dashboard_erpnext_contacts_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/erpnext/account-managers",
        dashboard_erpnext_account_managers_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/erpnext/cost-centers",
        dashboard_erpnext_cost_centers_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/projects/create",
        dashboard_create_project_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/bulk",
        dashboard_bulk_update_projects_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/{project_id}/status",
        dashboard_update_project_status_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/{project_id}/users",
        dashboard_add_project_user_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/{project_id}/users/remove",
        dashboard_remove_project_user_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/{project_id}/historical-members",
        dashboard_add_project_historical_member_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/{project_id}/historical-members/remove",
        dashboard_remove_project_historical_member_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/projects/{project_id}/wiki-match",
        dashboard_update_project_wiki_match_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/sync/projects",
        dashboard_sync_projects_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gigs/{engagement_id}/status",
        dashboard_update_gig_status_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gigs/{engagement_id}/applications",
        dashboard_add_gig_application_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/gigs/{engagement_id}/applications/{application_id}/status",
        dashboard_update_gig_application_status_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding",
        dashboard_onboarding_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/volunteers",
        dashboard_onboarding_volunteers_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/volunteers/{contact_id}",
        dashboard_onboarding_volunteer_handler,
        methods=["PUT"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/{contact_id}/suggestions",
        dashboard_onboarding_suggestions_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/engineers",
        dashboard_setup_engineer_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/{contact_id}/onboarder",
        dashboard_assign_onboarder_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/{contact_id}/status",
        dashboard_update_onboarding_status_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/{contact_id}/email/draft",
        dashboard_onboarding_email_draft_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/{contact_id}/email/send",
        dashboard_onboarding_email_send_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/audit-events",
        dashboard_audit_events_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/agent",
        dashboard_agent_report_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/configuration",
        dashboard_configuration_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/discord-diagnostics",
        dashboard_discord_diagnostics_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/configuration/{key}",
        dashboard_update_configuration_handler,
        methods=["PUT"],
    )
    app.add_api_route(
        "/dashboard/api/newsletter/suppressions",
        dashboard_newsletter_suppressions_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/newsletter/status",
        dashboard_newsletter_status_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/sync/people",
        dashboard_sync_people_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/sync/newsletters",
        dashboard_sync_newsletters_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/gigs/{item_id}",
        dashboard_handler,
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route(
        "/dashboard/projects/{item_id}",
        dashboard_handler,
        methods=["GET"],
        response_model=None,
    )

    app.add_api_route("/jobs", jobs_handler, methods=["GET"])
    app.add_api_route("/jobs/{job_id}", job_status_handler, methods=["GET"])
    app.add_api_route("/jobs/{job_id}/rerun", rerun_job_handler, methods=["POST"])
    app.add_api_route("/jobs/resume-extract", resume_extract_handler, methods=["POST"])
    app.add_api_route("/jobs/resume-apply", resume_apply_handler, methods=["POST"])

    app.add_api_route("/webhooks/espocrm", espocrm_webhook_handler, methods=["POST"])
    app.add_api_route(
        "/webhooks/espocrm/people-sync",
        espocrm_people_sync_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/webhooks/docuseal",
        docuseal_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/webhooks/google-forms",
        google_forms_intake_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/webhooks/tally",
        tally_intake_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/webhooks/tally/onboarding",
        tally_intake_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route("/webhooks/{source}", ingest_handler, methods=["POST"])

    app.add_api_route(
        "/process-contact/{contact_id}",
        process_contact_handler,
        methods=["POST"],
    )
    app.add_api_route("/sync/people", sync_people_handler, methods=["POST"])
    app.add_api_route("/audit/events", audit_event_handler, methods=["POST"])
    app.add_api_route("/agent/requests", agent_request_handler, methods=["POST"])
    app.add_api_route(
        "/agent/confirmations/{plan_id}",
        agent_confirmation_handler,
        methods=["POST"],
    )

    app.add_api_route(
        "/auth/login", auth_login_handler, methods=["GET"], response_model=None
    )
    app.add_api_route(
        "/auth/callback", auth_callback_handler, methods=["GET"], response_model=None
    )
    app.add_api_route("/auth/me", auth_me_handler, methods=["GET"])
    app.add_api_route("/auth/logout", auth_logout_handler, methods=["POST"])
    app.add_api_route(
        "/auth/discord/links",
        auth_discord_link_create_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/auth/discord/link/{token}",
        auth_discord_link_redirect_handler,
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route(
        "/auth/discord/link/{token}/consume",
        auth_discord_link_consume_handler,
        methods=["POST"],
        response_model=None,
    )
