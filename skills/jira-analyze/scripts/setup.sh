#!/usr/bin/env bash
# setup.sh — jira-analyze skill setup hook.
#
# Guides the user through configuring Jira API Token credentials.
# Run this once after installing the skill.

set -eu

HERMES_ENV="${HERMES_HOME:-$HOME/.hermes}/.env"
if [ ! -f "$HERMES_ENV" ]; then
    touch "$HERMES_ENV"
fi

echo "============================================"
echo "  jira-analyze Skill Setup"
echo "============================================"
echo ""
echo "This skill needs 3 environment variables to work:"
echo ""
echo "  1. JIRA_USER_EMAIL  — Your Atlassian account email"
echo "  2. JIRA_API_TOKEN   — From https://id.atlassian.com/manage-profile/security/api-tokens"
echo "  3. JIRA_CLOUD_ID    — From https://<your-site>.atlassian.net/secure/admin/cloudid"
echo ""
echo "============================================"

# Check if already configured
has_email=$(grep -c "^JIRA_USER_EMAIL=" "$HERMES_ENV" 2>/dev/null || echo 0)
has_token=$(grep -c "^JIRA_API_TOKEN=" "$HERMES_ENV" 2>/dev/null || echo 0)
has_cloud=$(grep -c "^JIRA_CLOUD_ID=" "$HERMES_ENV" 2>/dev/null || echo 0)

if [ "$has_email" -gt 0 ] && [ "$has_token" -gt 0 ] && [ "$has_cloud" -gt 0 ]; then
    echo "✅ All 3 env vars already configured in $HERMES_ENV"
    echo "   To reconfigure, delete the lines and re-run this script."
    exit 0
fi

echo ""
echo "Enter your Jira credentials (leave blank to skip):"
echo ""

# JIRA_USER_EMAIL
if [ "$has_email" -eq 0 ]; then
    read -r -p "JIRA_USER_EMAIL: " email
    if [ -n "$email" ]; then
        echo "JIRA_USER_EMAIL=$email" >> "$HERMES_ENV"
        echo "  ✅ Saved"
    fi
else
    echo "JIRA_USER_EMAIL: already configured ✅"
fi

# JIRA_API_TOKEN
if [ "$has_token" -eq 0 ]; then
    read -r -s -p "JIRA_API_TOKEN (hidden): " token
    echo ""
    if [ -n "$token" ]; then
        echo "JIRA_API_TOKEN=$token" >> "$HERMES_ENV"
        echo "  ✅ Saved"
    fi
else
    echo "JIRA_API_TOKEN: already configured ✅"
fi

# JIRA_CLOUD_ID
if [ "$has_cloud" -eq 0 ]; then
    read -r -p "JIRA_CLOUD_ID: " cloud
    if [ -n "$cloud" ]; then
        echo "JIRA_CLOUD_ID=$cloud" >> "$HERMES_ENV"
        echo "  ✅ Saved"
    fi
else
    echo "JIRA_CLOUD_ID: already configured ✅"
fi

echo ""
echo "============================================"
echo "  Setup complete! 🎉"
echo "  Restart Hermes (or /reset) for changes to take effect."
echo "============================================"
