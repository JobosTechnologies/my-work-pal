# WP6 — The Google sitting: what the owner does, in order

One short sitting connects the pilot owner's real Gmail and Calendar.
Everything else is already built: the connect flow
(`shared/scripts/google_oauth.py`), the pilot connections file
(`shared/config/connections.pilot.yaml.example`, shape proven with
`connection_health.py --config-only`), the health checks, and provider
execution — since WP13 the `google_api` provider acts with the owner's
stored token whenever the connections file declares
`google.auth.mode: oauth_user` (refreshing an expired access token
itself; `service_account` configs keep the delegation route). Only the
steps below need the owner present, because they are bound to his
Google account. Each step is one action on one screen.

Before the sitting: never paste the client secret into chat, email, or
any file — it goes only into the secure capture the agent opens.

1. Open console.cloud.google.com and pick the project **My Work Pal**.
2. Go to **APIs & Services → Credentials** and click the client named **My Work Pal Web**.
3. On that page, copy the **Client ID** into the secure capture as `MWP_GOOGLE_OAUTH_CLIENT_ID`.
4. On the same page, copy the **Client secret** into the secure capture as `MWP_GOOGLE_OAUTH_CLIENT_SECRET`.
5. Still on that page, under **Authorized redirect URIs**, click **Add URI** and paste exactly: `https://labs.jobos.us/oauth/google/callback`
6. Click **Save** at the bottom of the page.
7. Go to **APIs & Services → OAuth consent screen** and confirm the publishing status still says **Testing**.
8. On the same screen, under **Test users**, click **Add users**, type the pilot owner's Google address, and save.
9. Tell the agent the pilot owner's Google address (the same one) so it replaces the placeholder in the connections file.
10. When the agent gives you a sign-in link, open it, sign in as the pilot owner, click **Continue** on the "Google hasn't verified this app" notice (expected while Testing), and click **Allow**.

That is the whole sitting. Afterwards the agent runs the health check —
it must show the client configured and the token stored — and the pilot
account is connected.
