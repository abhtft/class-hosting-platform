-- Makes a COPY of the live Chatwoot database harmless: it can no longer reach out to a customer, a mailbox, a bot or another system.
-- Run it on the replica right after the restore and BEFORE Chatwoot and Sidekiq start. It is never run on the real installation, and the cutover restores a fresh dump over it.
-- Every statement only blanks something that sends, receives or calls out; no conversation, message or contact is changed.
\set ON_ERROR_STOP on
BEGIN;
-- bots and webhooks: no call to the bot adapter, to n8n or to any other address
UPDATE agent_bots SET outgoing_url = '', secret = NULL;
UPDATE webhooks SET url = 'https://example.invalid/disabled', secret = NULL;
UPDATE channel_api SET webhook_url = '', hmac_token = NULL, secret = NULL;
-- WhatsApp, Facebook, Instagram, Telegram, Twilio, SMS, Line, TikTok, Twitter: no token, so nothing can be sent to a real customer
UPDATE channel_whatsapp SET provider_config = '{}', business_management_token = NULL;
UPDATE channel_facebook_pages SET page_access_token = 'disabled', user_access_token = 'disabled';
UPDATE channel_instagram SET access_token = 'disabled';
UPDATE channel_telegram SET bot_token = 'disabled';
UPDATE channel_twilio_sms SET auth_token = 'disabled', api_key_secret = NULL, api_key_sid = NULL, provider_config = '{}';
UPDATE channel_sms SET provider_config = '{}';
UPDATE channel_line SET line_channel_secret = 'disabled', line_channel_token = 'disabled';
UPDATE channel_tiktok SET access_token = 'disabled', refresh_token = 'disabled';
UPDATE channel_twitter_profiles SET twitter_access_token = 'disabled', twitter_access_token_secret = 'disabled';
-- e-mail channels: no mailbox is read (IMAP) and nothing is sent (SMTP)
UPDATE channel_email SET imap_enabled = false, smtp_enabled = false, imap_password = NULL, smtp_password = NULL, provider_config = '{}';
-- integrations (Slack, Dialogflow, Linear and so on): no token
UPDATE integrations_hooks SET access_token = 'disabled';
COMMIT;
