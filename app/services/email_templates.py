"""Default transactional email templates in FR and EN (section 7).

Subjects and bodies use {variable} placeholders so the owner can edit them in admin (EF-69).
Variables ending in `_html` are pre-rendered HTML blocks (order summary, schedules); all
other values are HTML-escaped before substitution.
"""

TEMPLATES: dict[str, dict] = {
    "welcome_verify": {
        "variables": ["first_name", "verify_url"],
        "fr": (
            "Bienvenue, {first_name} ! Confirmez votre courriel",
            "<p>Bonjour {first_name},</p><p>Bienvenue ! Votre compte a bien été créé.</p>"
            "<p>Confirmez votre adresse courriel pour accéder aux liens de vos séances :</p>"
            '<p><a class="btn" href="{verify_url}">Confirmer mon courriel</a></p>'
            "<p>Ce lien est valide 48 heures.</p>",
        ),
        "en": (
            "Welcome, {first_name}! Confirm your email",
            "<p>Hi {first_name},</p><p>Welcome! Your account has been created.</p>"
            "<p>Confirm your email address to access your session links:</p>"
            '<p><a class="btn" href="{verify_url}">Confirm my email</a></p>'
            "<p>This link is valid for 48 hours.</p>",
        ),
    },
    "verify_email": {
        "variables": ["first_name", "verify_url"],
        "fr": (
            "Confirmez votre adresse courriel",
            "<p>Bonjour {first_name},</p><p>Cliquez sur le bouton ci-dessous pour confirmer votre adresse courriel.</p>"
            '<p><a class="btn" href="{verify_url}">Confirmer mon courriel</a></p>',
        ),
        "en": (
            "Confirm your email address",
            "<p>Hi {first_name},</p><p>Click the button below to confirm your email address.</p>"
            '<p><a class="btn" href="{verify_url}">Confirm my email</a></p>',
        ),
    },
    "password_reset": {
        "variables": ["first_name", "reset_url"],
        "fr": (
            "Réinitialisation de votre mot de passe",
            "<p>Bonjour {first_name},</p><p>Vous avez demandé à réinitialiser votre mot de passe.</p>"
            '<p><a class="btn" href="{reset_url}">Choisir un nouveau mot de passe</a></p>'
            "<p>Ce lien est valide 60 minutes. Si vous n'êtes pas à l'origine de cette demande, ignorez ce courriel.</p>",
        ),
        "en": (
            "Reset your password",
            "<p>Hi {first_name},</p><p>You asked to reset your password.</p>"
            '<p><a class="btn" href="{reset_url}">Choose a new password</a></p>'
            "<p>This link is valid for 60 minutes. If you did not make this request, ignore this email.</p>",
        ),
    },
    "email_change": {
        "variables": ["first_name", "confirm_url", "new_email"],
        "fr": (
            "Confirmez votre nouvelle adresse courriel",
            "<p>Bonjour {first_name},</p><p>Confirmez que {new_email} est votre nouvelle adresse courriel :</p>"
            '<p><a class="btn" href="{confirm_url}">Confirmer</a></p>',
        ),
        "en": (
            "Confirm your new email address",
            "<p>Hi {first_name},</p><p>Confirm that {new_email} is your new email address:</p>"
            '<p><a class="btn" href="{confirm_url}">Confirm</a></p>',
        ),
    },
    "order_confirmed": {
        "variables": ["first_name", "order_number", "order_summary_html", "schedule_html", "dashboard_url", "terms_url", "refund_policy_url"],
        "fr": (
            "Inscription confirmée — commande {order_number}",
            "<p>Bonjour {first_name},</p><p>Merci ! Votre paiement est confirmé et votre inscription est réservée.</p>"
            "<h3>Résumé de la commande {order_number}</h3>{order_summary_html}"
            "<h3>Vos cohortes</h3>{schedule_html}"
            "<p>Votre facture PDF et un fichier calendrier (.ics) sont joints à ce courriel.</p>"
            '<p><a class="btn" href="{dashboard_url}">Aller à mon tableau de bord</a></p>'
            '<p class="muted">Conditions de vente : <a href="{terms_url}">{terms_url}</a><br>'
            'Politique d\'annulation et de remboursement : <a href="{refund_policy_url}">{refund_policy_url}</a></p>',
        ),
        "en": (
            "Enrollment confirmed — order {order_number}",
            "<p>Hi {first_name},</p><p>Thank you! Your payment is confirmed and your seat is reserved.</p>"
            "<h3>Order {order_number} summary</h3>{order_summary_html}"
            "<h3>Your cohorts</h3>{schedule_html}"
            "<p>Your PDF invoice and a calendar file (.ics) are attached to this email.</p>"
            '<p><a class="btn" href="{dashboard_url}">Go to my dashboard</a></p>'
            '<p class="muted">Terms of sale: <a href="{terms_url}">{terms_url}</a><br>'
            'Cancellation and refund policy: <a href="{refund_policy_url}">{refund_policy_url}</a></p>',
        ),
    },
    "cohort_prep": {
        "variables": ["first_name", "bootcamp_title", "cohort_start", "first_session", "preparation_html", "prerequisites_html", "tools", "dashboard_url"],
        "fr": (
            "{bootcamp_title} commence dans 7 jours",
            "<p>Bonjour {first_name},</p><p>Votre bootcamp <strong>{bootcamp_title}</strong> commence le {cohort_start}.</p>"
            "<p><strong>Première séance :</strong> {first_session}</p>"
            "<h3>Liste de préparation</h3>{preparation_html}<h3>Préalables</h3>{prerequisites_html}"
            "<h3>Outils à installer</h3><p>{tools}</p>"
            '<p><a class="btn" href="{dashboard_url}">Voir mon tableau de bord</a></p>',
        ),
        "en": (
            "{bootcamp_title} starts in 7 days",
            "<p>Hi {first_name},</p><p>Your bootcamp <strong>{bootcamp_title}</strong> starts on {cohort_start}.</p>"
            "<p><strong>First session:</strong> {first_session}</p>"
            "<h3>Preparation checklist</h3>{preparation_html}<h3>Prerequisites</h3>{prerequisites_html}"
            "<h3>Tools to install</h3><p>{tools}</p>"
            '<p><a class="btn" href="{dashboard_url}">See my dashboard</a></p>',
        ),
    },
    "session_reminder": {
        "variables": ["first_name", "bootcamp_title", "session_title", "session_time", "when", "join_url"],
        "fr": (
            "Rappel : séance {when} — {bootcamp_title}",
            "<p>Bonjour {first_name},</p><p>Votre séance <strong>{session_title}</strong> de {bootcamp_title} commence {when}.</p>"
            "<p><strong>Heure :</strong> {session_time}</p>"
            '<p><a class="btn" href="{join_url}">Rejoindre la séance</a></p>',
        ),
        "en": (
            "Reminder: session {when} — {bootcamp_title}",
            "<p>Hi {first_name},</p><p>Your session <strong>{session_title}</strong> of {bootcamp_title} starts {when}.</p>"
            "<p><strong>Time:</strong> {session_time}</p>"
            '<p><a class="btn" href="{join_url}">Join the session</a></p>',
        ),
    },
    "session_changed": {
        "variables": ["first_name", "bootcamp_title", "session_title", "change", "old_time", "new_time", "dashboard_url"],
        "fr": (
            "Changement d'horaire — {bootcamp_title}",
            "<p>Bonjour {first_name},</p><p>La séance <strong>{session_title}</strong> de {bootcamp_title} a été {change}.</p>"
            "<p><strong>Ancienne date :</strong> {old_time}<br><strong>Nouvelle date :</strong> {new_time}</p>"
            "<p>Un fichier calendrier mis à jour est joint.</p>"
            '<p><a class="btn" href="{dashboard_url}">Voir mon horaire</a></p>',
        ),
        "en": (
            "Schedule change — {bootcamp_title}",
            "<p>Hi {first_name},</p><p>The session <strong>{session_title}</strong> of {bootcamp_title} has been {change}.</p>"
            "<p><strong>Previous date:</strong> {old_time}<br><strong>New date:</strong> {new_time}</p>"
            "<p>An updated calendar file is attached.</p>"
            '<p><a class="btn" href="{dashboard_url}">See my schedule</a></p>',
        ),
    },
    "material_published": {
        "variables": ["first_name", "bootcamp_title", "material_title", "dashboard_url"],
        "fr": (
            "Nouveau matériel : {material_title}",
            "<p>Bonjour {first_name},</p><p>Du nouveau matériel est disponible pour {bootcamp_title} : <strong>{material_title}</strong>.</p>"
            '<p><a class="btn" href="{dashboard_url}">Voir le matériel</a></p>',
        ),
        "en": (
            "New material: {material_title}",
            "<p>Hi {first_name},</p><p>New material is available for {bootcamp_title}: <strong>{material_title}</strong>.</p>"
            '<p><a class="btn" href="{dashboard_url}">See the material</a></p>',
        ),
    },
    "waitlist_joined": {
        "variables": ["first_name", "bootcamp_title", "cohort_start", "position"],
        "fr": (
            "Vous êtes sur la liste d'attente — {bootcamp_title}",
            "<p>Bonjour {first_name},</p><p>Vous êtes inscrit à la liste d'attente de {bootcamp_title} (début le {cohort_start}).</p>"
            "<p>Position : {position}. Nous vous écrirons dès qu'une place se libère.</p>",
        ),
        "en": (
            "You're on the waitlist — {bootcamp_title}",
            "<p>Hi {first_name},</p><p>You joined the waitlist for {bootcamp_title} (starting {cohort_start}).</p>"
            "<p>Position: {position}. We'll email you as soon as a seat opens up.</p>",
        ),
    },
    "waitlist_offer": {
        "variables": ["first_name", "bootcamp_title", "cohort_start", "offer_url", "expires_at"],
        "fr": (
            "Une place s'est libérée — {bootcamp_title}",
            "<p>Bonjour {first_name},</p><p>Bonne nouvelle : une place est réservée pour vous dans {bootcamp_title} (début le {cohort_start}).</p>"
            '<p><a class="btn" href="{offer_url}">Réserver ma place</a></p>'
            "<p>Cette offre est valide jusqu'au {expires_at}. Passé ce délai, la place sera offerte à la personne suivante.</p>",
        ),
        "en": (
            "A seat opened up — {bootcamp_title}",
            "<p>Hi {first_name},</p><p>Good news: a seat is reserved for you in {bootcamp_title} (starting {cohort_start}).</p>"
            '<p><a class="btn" href="{offer_url}">Claim my seat</a></p>'
            "<p>This offer is valid until {expires_at}. After that, the seat will be offered to the next person.</p>",
        ),
    },
    "refund_processed": {
        "variables": ["first_name", "order_number", "amount", "payment_method", "enrollment_status"],
        "fr": (
            "Remboursement traité — commande {order_number}",
            "<p>Bonjour {first_name},</p><p>Un remboursement de <strong>{amount}</strong> a été émis pour la commande {order_number}.</p>"
            "<p><strong>Mode de paiement :</strong> {payment_method}<br><strong>Statut de l'inscription :</strong> {enrollment_status}</p>"
            "<p>Le montant apparaîtra sur votre relevé d'ici 5 à 10 jours ouvrables.</p>",
        ),
        "en": (
            "Refund processed — order {order_number}",
            "<p>Hi {first_name},</p><p>A refund of <strong>{amount}</strong> was issued for order {order_number}.</p>"
            "<p><strong>Payment method:</strong> {payment_method}<br><strong>Enrollment status:</strong> {enrollment_status}</p>"
            "<p>The amount will appear on your statement within 5 to 10 business days.</p>",
        ),
    },
    "cancellation_decision": {
        "variables": ["first_name", "bootcamp_title", "decision", "admin_note"],
        "fr": (
            "Votre demande d'annulation — {bootcamp_title}",
            "<p>Bonjour {first_name},</p><p>Votre demande d'annulation pour {bootcamp_title} a été {decision}.</p><p>{admin_note}</p>",
        ),
        "en": (
            "Your cancellation request — {bootcamp_title}",
            "<p>Hi {first_name},</p><p>Your cancellation request for {bootcamp_title} has been {decision}.</p><p>{admin_note}</p>",
        ),
    },
    "certificate_ready": {
        "variables": ["first_name", "bootcamp_title", "dashboard_url"],
        "fr": (
            "Votre attestation de réussite est prête",
            "<p>Bonjour {first_name},</p><p>Félicitations pour avoir terminé {bootcamp_title} !</p>"
            '<p><a class="btn" href="{dashboard_url}">Télécharger mon attestation</a></p>',
        ),
        "en": (
            "Your certificate of completion is ready",
            "<p>Hi {first_name},</p><p>Congratulations on completing {bootcamp_title}!</p>"
            '<p><a class="btn" href="{dashboard_url}">Download my certificate</a></p>',
        ),
    },
    "enrollment_manual": {
        "variables": ["first_name", "bootcamp_title", "cohort_start", "dashboard_url"],
        "fr": (
            "Vous êtes inscrit — {bootcamp_title}",
            "<p>Bonjour {first_name},</p><p>Vous êtes maintenant inscrit à {bootcamp_title} (début le {cohort_start}).</p>"
            '<p><a class="btn" href="{dashboard_url}">Aller à mon tableau de bord</a></p>',
        ),
        "en": (
            "You're enrolled — {bootcamp_title}",
            "<p>Hi {first_name},</p><p>You are now enrolled in {bootcamp_title} (starting {cohort_start}).</p>"
            '<p><a class="btn" href="{dashboard_url}">Go to my dashboard</a></p>',
        ),
    },
    "staff_invite": {
        "variables": ["first_name", "role", "invite_url"],
        "fr": (
            "Invitation à l'administration du site",
            "<p>Bonjour {first_name},</p><p>Vous êtes invité à rejoindre le back-office avec le rôle <strong>{role}</strong>.</p>"
            '<p><a class="btn" href="{invite_url}">Activer mon compte</a></p>'
            "<p>Vous devrez configurer l'authentification à deux facteurs à la première connexion.</p>",
        ),
        "en": (
            "Invitation to the site administration",
            "<p>Hi {first_name},</p><p>You are invited to join the back office with the <strong>{role}</strong> role.</p>"
            '<p><a class="btn" href="{invite_url}">Activate my account</a></p>'
            "<p>You will need to set up two-factor authentication on first sign-in.</p>",
        ),
    },
    "contact_autoreply": {
        "variables": ["name", "subject"],
        "fr": (
            "Nous avons bien reçu votre message",
            "<p>Bonjour {name},</p><p>Merci de nous avoir écrit au sujet de « {subject} ». Nous vous répondrons sous peu.</p>",
        ),
        "en": (
            "We received your message",
            "<p>Hi {name},</p><p>Thanks for writing to us about “{subject}”. We'll get back to you shortly.</p>",
        ),
    },
    "admin_alert": {
        "variables": ["alert_title", "alert_body_html", "admin_url"],
        "fr": (
            "[Admin] {alert_title}",
            "<p><strong>{alert_title}</strong></p>{alert_body_html}"
            '<p><a class="btn" href="{admin_url}">Ouvrir dans l\'admin</a></p>',
        ),
        "en": (
            "[Admin] {alert_title}",
            "<p><strong>{alert_title}</strong></p>{alert_body_html}"
            '<p><a class="btn" href="{admin_url}">Open in admin</a></p>',
        ),
    },
    "account_deleted": {
        "variables": ["first_name"],
        "fr": (
            "Votre compte a été supprimé",
            "<p>Bonjour {first_name},</p><p>Votre demande a été traitée : vos renseignements personnels ont été supprimés ou anonymisés. "
            "Les factures sont conservées comme l'exige la loi.</p>",
        ),
        "en": (
            "Your account has been deleted",
            "<p>Hi {first_name},</p><p>Your request has been processed: your personal information has been deleted or anonymized. "
            "Invoices are retained as required by law.</p>",
        ),
    },
}
