/**
 * Campus Lost & Found: email relay for hosts that block SMTP (e.g. Render's free tier).
 *
 * Setup (once):
 *  1. https://script.google.com -> New project, replace the editor contents with this file, Save.
 *  2. Project Settings (gear) -> Script properties -> Add script property:
 *       SECRET = the value of APPS_SCRIPT_MAIL_SECRET from Render (service -> Environment).
 *  3. Deploy -> New deployment -> type "Web app":
 *       Execute as: Me      Who has access: Anyone
 *     Allow the "send email as you" permission when Google asks, then copy the Web app URL
 *     (it ends with /exec) into Render as APPS_SCRIPT_MAIL_URL.
 *
 * Mail is sent from the Google account that deployed the script (about 100 emails/day on a
 * personal account). Requests without the correct secret are refused. Nothing is logged.
 */
function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply_({ ok: false, code: 'bad_json' });
  }
  var secret = PropertiesService.getScriptProperties().getProperty('SECRET');
  if (!secret || data.secret !== secret) {
    return reply_({ ok: false, code: 'bad_secret' });
  }
  if (data.action === 'ping') {
    return reply_({ ok: true, quota: MailApp.getRemainingDailyQuota() });
  }
  if (data.action !== 'send' || !data.to || !data.subject) {
    return reply_({ ok: false, code: 'bad_request' });
  }
  if (MailApp.getRemainingDailyQuota() < 1) {
    return reply_({ ok: false, code: 'quota_exceeded' });
  }
  var options = { name: data.name || 'Campus Lost & Found' };
  if (data.html) options.htmlBody = data.html;
  if (data.replyTo) options.replyTo = data.replyTo;
  try {
    MailApp.sendEmail(data.to, data.subject, data.body || '', options);
  } catch (err) {
    return reply_({ ok: false, code: 'send_failed' });
  }
  return reply_({ ok: true });
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
