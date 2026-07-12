---
title: Contact Verdify
description: Send a question about the public proof record.
tags: [contact]
---

# Contact Verdify

<form class="verdify-contact-form">
  <div class="verdify-contact-grid">
    <label><span>Name</span><input name="name" autocomplete="name" required></label>
    <label><span>Reply email</span><input name="email" type="email" autocomplete="email" required></label>
  </div>
  <label><span>Topic</span><select name="topic"><option>Public proof record</option></select></label>
  <label><span>Message</span><textarea name="message" required></textarea></label>
  <div class="verdify-contact-actions">
    <button type="submit">Send message</button>
    <p id="verdify-contact-status" role="status" aria-live="polite"></p>
  </div>
</form>
