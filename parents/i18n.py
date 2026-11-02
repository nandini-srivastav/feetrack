"""Parent-facing copy in Hindi and English.

Kept as plain dictionaries so a non-developer can review the Hindi in one
file. Hindi is the default; parents switch with the हिं / EN toggle.
"""
STRINGS = {
    "amount_due": ("बकाया राशि", "Amount due"),
    "due_on": ("देय तिथि", "Due date"),
    "days_late": ("{n} दिन देर", "{n} days overdue"),
    "due_in": ("{n} दिन में देय", "Due in {n} days"),
    "due_today": ("आज देय", "Due today"),
    "pay": ("{amount} का भुगतान करें", "Pay {amount}"),
    "pay_note": ("UPI, कार्ड या नेट बैंकिंग · रसीद तुरंत मिलेगी", "UPI, card or net banking · instant receipt"),
    "next": ("अगली किस्त", "Next instalment"),
    "paid_year": ("इस वर्ष जमा", "Paid this year"),
    "all_paid": ("सब भुगतान हो गया", "All paid up"),
    "all_paid_sub": ("अभी कुछ बकाया नहीं है।", "Nothing is due right now."),
    "tab_dues": ("बकाया", "Dues"),
    "tab_receipts": ("रसीदें", "Receipts"),
    "tab_progress": ("पढ़ाई", "Progress"),
    "receipts": ("रसीदें", "Receipts"),
    "no_receipts": ("अभी कोई रसीद नहीं", "No receipts yet"),
    "download": ("रसीद डाउनलोड करें", "Download receipt"),
    "attendance": ("उपस्थिति", "Attendance"),
    "last_4_weeks": ("पिछले 4 सप्ताह", "Last 4 weeks"),
    "tests": ("टेस्ट के अंक", "Test scores"),
    "batch_avg": ("बैच औसत", "batch avg"),
    "no_progress": ("अभी उपस्थिति या अंक नहीं जुड़े", "No attendance or scores yet"),
    "sign_out": ("लॉग आउट", "Sign out"),
    "wk": ("सप्ताह", "Wk"),
    "instalment": ("किस्त", "Instalment"),
    "withdrawn": ("नामांकन समाप्त", "Withdrawn"),
    "help": ("सवाल हो तो केंद्र से संपर्क करें", "Questions? Contact your centre"),
    "switch_child": ("बच्चा चुनें", "Choose child"),
    "withdrawn_sub": ("इस नामांकन पर अब कोई किस्त देय नहीं है।", "No more instalments are due on this enrolment."),
    # sign in
    "login_title": ("अभिभावक लॉगिन", "Parent sign in"),
    "login_sub": ("जिस मोबाइल नंबर पर केंद्र आपको संदेश भेजता है, वही डालें।",
                  "Use the mobile number the centre sends messages to."),
    "phone_label": ("मोबाइल नंबर", "Mobile number"),
    "send_code": ("OTP भेजें", "Send OTP"),
    "code_label": ("6 अंकों का OTP", "6-digit OTP"),
    "code_sent": ("OTP भेजा गया: {n}", "OTP sent to {n}"),
    "verify": ("जारी रखें", "Continue"),
    "change_number": ("नंबर बदलें", "Change number"),
    "demo_code": ("डेमो: आपका OTP {n} है", "Demo: your OTP is {n}"),
    # consent (DPDP Act 2023)
    "consent_title": ("आपकी अनुमति", "Your consent"),
    "consent_body": ("आपका बच्चा 18 वर्ष से कम है। फीस, उपस्थिति और अंक दिखाने के लिए हमें आपकी अनुमति चाहिए। "
                     "यह जानकारी केवल फीस और पढ़ाई की प्रगति के लिए उपयोग होगी।",
                     "Your child is under 18. We need your consent to show their fees, attendance and scores. "
                     "This data is used only for fees and study progress."),
    "consent_agree": ("मैं अभिभावक हूँ और अनुमति देता/देती हूँ", "I am the parent and I give consent"),
    "continue": ("आगे बढ़ें", "Continue"),
    # pay (test gateway)
    "test_mode": ("टेस्ट भुगतान · असली पैसा नहीं कटेगा", "Test payment · no real money moves"),
    "pay_for": ("किस्त {n}", "Instalment {n}"),
    "method": ("भुगतान का तरीका", "Payment method"),
    "simulate": ("टेस्ट के लिए दूसरा परिणाम", "Try another outcome (testing)"),
    "simulate_fail": ("असफल भुगतान", "Failed payment"),
    "simulate_diff": ("अलग राशि भेजें", "Send a different amount"),
    "link_used": ("यह लिंक पहले ही उपयोग हो चुका है।", "This link has already been used."),
    "paid_title": ("{n} जमा हुए", "{n} paid"),
    "paid_sub": ("यह किस्त पूरी हो गई। इसके लिए अब कोई रिमाइंडर नहीं आएगा।",
                 "This instalment is fully paid. No more reminders will be sent for it."),
    "paid_part": ("भुगतान जमा हुआ। बाकी राशि अभी देय है।", "Payment received. The rest is still due."),
    "failed_title": ("भुगतान नहीं हुआ", "Payment did not go through"),
    "failed_sub": ("कोई पैसा नहीं कटा। फिर से कोशिश करें।", "No money was taken. Please try again."),
    "unmatched_title": ("भुगतान मिला, जाँच जारी", "Payment received, being checked"),
    "unmatched_sub": ("राशि किस्त से मेल नहीं खाती, इसलिए हमारी टीम इसे 1 कार्य दिवस में जोड़ेगी। तब रसीद मिलेगी।",
                      "The amount didn’t match the instalment, so our accounts team will apply it within "
                      "1 working day and send your receipt then."),
    "try_again": ("फिर से कोशिश करें", "Try again"),
    "back_to_dues": ("बकाया पर वापस", "Back to dues"),
    "receipt_no": ("रसीद संख्या", "Receipt no."),
    "taxable": ("कर योग्य राशि", "Taxable value"),
    "total": ("कुल", "Total"),
}


def strings(lang):
    idx = 0 if lang == "hi" else 1
    return {k: v[idx] for k, v in STRINGS.items()}
