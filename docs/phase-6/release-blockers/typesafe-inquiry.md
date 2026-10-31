# Draft inquiry to TypeSafe (not sent)

To: privacy@typesafe.ai, cc support@typesafe.ai
Subject: Data handling of System One API request bodies (self-serve account)

Hello,

OXIDE is an open-source code-context tool. It can optionally send excerpts of a user's
source code to `POST /v1/systemone` (`jev-1.14.0`). The feature is off by default, and
users enable it per repository. Before we release it, we need to describe accurately to
users what happens to the code they send. We have read the Master Customer Agreement
(last updated Sep 23, 2026), the DPA (Apr 24, 2026), the Privacy Policy (Nov 19, 2025)
and the Terms of Use (Sep 19, 2026). We understand that Input is not used to train or
fine-tune models (MCA § 4.1, Privacy Policy). We are not treating that as a statement
about retention. Could you confirm the following in writing?

1. **Governing agreement.** Does the MCA govern a self-serve account that creates an API
   key in console.typesafe.ai, with no signed order? If so, which "Order" applies (the
   checkout page, or a confirmation email)? Does the Terms of Use sentence "Do not submit
   any information or other materials that you consider confidential or proprietary
   through the Site" apply to API requests?
2. **Request-body retention.** Are request bodies (`state`, `questions`) and responses
   stored after the response is returned, including in logs, caches, queues, debugging
   records or backups? If so, for how long in each place, and in which country?
3. **Confidentiality.** Is Input submitted through the API Customer Confidential
   Information under MCA § 14? We read § 14.1 as covering information that "should be
   reasonably understood to be proprietary or confidential", and we expect proprietary
   source code to qualify.
4. **Telemetry.** Under MCA § 4.1(c) and § 4.3, can Telemetry contain any part of a
   request body, or content derived from it, such as text, hashes of excerpts, or
   "classifications" or "learnings" about the code? Could Telemetry be used to train or
   evaluate models, given the § 4.1 restriction on training with Customer Data?
5. **Deletion.** Can a self-serve customer have its stored request bodies and outputs
   deleted on request? What is the timeline, and does it include backups, which MCA
   § 10.3 says may be retained?
6. **Zero data retention.** Is zero data retention available to a self-serve or
   pay-as-you-go account, or only under an enterprise agreement? Can it be turned on per
   API key?
7. **Subprocessors.** Which subprocessors receive request bodies, and where? The list at
   trust.typesafe.ai could not be read without JavaScript.

We will quote your answers, attributed and dated, in OXIDE's documentation. We will only
describe the feature as having "zero retention" if you confirm that it does.

Thank you,
[maintainer name], OXIDE
