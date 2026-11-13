"""Starter data for local development: `python -m app.seed`

Creates an admin, an instructor, categories, three sample bootcamps with upcoming cohorts and sessions,
general FAQ, home blocks and placeholder legal pages (to be replaced by texts validated by a lawyer).
No testimonials are created: only real ones, with the student's consent, may be published.
Idempotent: existing records (matched by email/slug/key) are left untouched.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.security import hash_password
from app.models import Bootcamp, Category, Cohort, ContentBlock, FaqItem, Page, SyllabusItem, User
from app.services import calendar, site_settings

TZ = ZoneInfo("America/Toronto")
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "ChangeMe-Admin-2026"
INSTRUCTOR_EMAIL = "formateur@example.com"
INSTRUCTOR_PASSWORD = "ChangeMe-Formateur-2026"


def next_weekday(d: date, weekday: int) -> date:
    return d + timedelta(days=(weekday - d.weekday()) % 7 or 7)


def get_or_create_user(db, email, password, role, first, last, **extra) -> User:
    u = db.scalar(select(User).where(User.email == email))
    if u:
        return u
    u = User(email=email, password_hash=hash_password(password), role=role, first_name=first, last_name=last, locale="fr", email_verified_at=utcnow(), email_prefs={"reminders": True, "materials": True}, **extra)
    db.add(u)
    db.flush()
    return u


def category(db, fr, en, slug_fr, slug_en, pos) -> Category:
    c = db.scalar(select(Category).where(Category.slug_fr == slug_fr))
    if not c:
        c = Category(name_fr=fr, name_en=en, slug_fr=slug_fr, slug_en=slug_en, position=pos)
        db.add(c)
        db.flush()
    return c


BOOTCAMPS = [
    {
        "slug_fr": "devops-intensif", "slug_en": "devops-intensive", "cat": "devops",
        "title_fr": "DevOps intensif", "title_en": "Intensive DevOps",
        "tagline_fr": "Automatisez, déployez et surveillez vos applications comme une équipe professionnelle.",
        "tagline_en": "Automate, deploy and monitor your applications like a professional team.",
        "summary_fr": "Huit semaines en direct pour maîtriser Git, Docker, l'intégration continue, Kubernetes et l'observabilité.",
        "summary_en": "Eight live weeks to master Git, Docker, continuous integration, Kubernetes and observability.",
        "description_fr": "<p>Ce bootcamp vous fait passer du code qui fonctionne sur votre poste à une application déployée automatiquement, surveillée et reproductible. Chaque semaine combine deux séances en direct, un laboratoire guidé et un projet fil rouge.</p>",
        "description_en": "<p>This bootcamp takes you from code that works on your laptop to an application that is deployed automatically, monitored and reproducible. Each week combines two live sessions, a guided lab and a capstone project.</p>",
        "outcomes_fr": ["Conteneuriser une application avec Docker", "Construire un pipeline CI/CD avec GitHub Actions", "Déployer sur Kubernetes", "Mettre en place des journaux, métriques et alertes"],
        "outcomes_en": ["Containerize an application with Docker", "Build a CI/CD pipeline with GitHub Actions", "Deploy to Kubernetes", "Set up logs, metrics and alerts"],
        "prerequisites_fr": "<p>Aisance avec la ligne de commande et notions de programmation dans un langage au choix.</p>",
        "prerequisites_en": "<p>Comfort with the command line and basic programming in any language.</p>",
        "preparation_fr": "<ul><li>Installer Docker Desktop</li><li>Créer un compte GitHub</li><li>Installer VS Code</li></ul>",
        "preparation_en": "<ul><li>Install Docker Desktop</li><li>Create a GitHub account</li><li>Install VS Code</li></ul>",
        "tools": ["Git", "Docker", "GitHub Actions", "Kubernetes", "Terraform", "Grafana"],
        "level": "intermediate", "duration_weeks": 8, "language": "fr", "featured": True,
        "keywords": ["devops", "docker", "kubernetes", "ci/cd"],
        "weeks": [("Git et collaboration", "Git and collaboration"), ("Docker", "Docker"), ("Intégration continue", "Continuous integration"), ("Déploiement continu", "Continuous deployment"), ("Kubernetes 1", "Kubernetes 1"), ("Kubernetes 2", "Kubernetes 2"), ("Infrastructure en code", "Infrastructure as code"), ("Observabilité et projet final", "Observability and final project")],
        "cohorts": [(21, 2, 18, "18:30", "21:00", [1, 3], 1299_00, 1099_00), (90, 3, 25, "18:30", "21:00", [1, 3], 1299_00, None)],
    },
    {
        "slug_fr": "python-analyse-de-donnees", "slug_en": "python-data-analysis", "cat": "prog",
        "title_fr": "Python pour l'analyse de données", "title_en": "Python for Data Analysis",
        "tagline_fr": "Passez de vos tableurs à des analyses reproductibles en Python.",
        "tagline_en": "Move from spreadsheets to reproducible analyses in Python.",
        "summary_fr": "Six semaines pour apprendre Python, pandas et la visualisation à partir de jeux de données réels.",
        "summary_en": "Six weeks to learn Python, pandas and visualization with real datasets.",
        "description_fr": "<p>Un bootcamp conçu pour les professionnels qui manipulent des données au quotidien. Aucune expérience en programmation n'est requise.</p>",
        "description_en": "<p>A bootcamp designed for professionals who work with data every day. No programming experience required.</p>",
        "outcomes_fr": ["Écrire des scripts Python clairs", "Nettoyer et transformer des données avec pandas", "Produire des graphiques lisibles", "Automatiser un rapport"],
        "outcomes_en": ["Write clear Python scripts", "Clean and transform data with pandas", "Produce readable charts", "Automate a report"],
        "prerequisites_fr": "<p>Aucun préalable en programmation. Une bonne connaissance d'Excel est un atout.</p>",
        "prerequisites_en": "<p>No programming prerequisites. Good Excel skills are an asset.</p>",
        "preparation_fr": "<ul><li>Installer Python 3.12</li><li>Installer VS Code</li></ul>",
        "preparation_en": "<ul><li>Install Python 3.12</li><li>Install VS Code</li></ul>",
        "tools": ["Python", "Jupyter", "pandas", "matplotlib"],
        "level": "beginner", "duration_weeks": 6, "language": "fr", "featured": True,
        "keywords": ["python", "données", "data", "pandas"],
        "weeks": [("Bases de Python", "Python basics"), ("Structures de données", "Data structures"), ("pandas 1", "pandas 1"), ("pandas 2", "pandas 2"), ("Visualisation", "Visualization"), ("Projet final", "Final project")],
        "cohorts": [(14, 0, 20, "18:00", "20:30", [0, 2], 999_00, None), (60, 1, 20, "09:00", "12:00", [5], 999_00, 849_00)],
    },
    {
        "slug_fr": "aws-fondamentaux", "slug_en": "aws-fundamentals", "cat": "cloud",
        "title_fr": "AWS : les fondamentaux", "title_en": "AWS Fundamentals",
        "tagline_fr": "Comprenez et utilisez les services essentiels d'AWS en toute confiance.",
        "tagline_en": "Understand and use the core AWS services with confidence.",
        "summary_fr": "Quatre semaines pour bâtir une architecture simple, sécurisée et économique sur AWS.",
        "summary_en": "Four weeks to build a simple, secure and cost-effective architecture on AWS.",
        "description_fr": "<p>Comptes, IAM, réseau, calcul, stockage et bases de données : vous construisez pas à pas une application web hébergée sur AWS.</p>",
        "description_en": "<p>Accounts, IAM, networking, compute, storage and databases: you build a web application hosted on AWS step by step.</p>",
        "outcomes_fr": ["Sécuriser un compte AWS avec IAM", "Déployer une application sur EC2 et S3", "Concevoir un réseau VPC", "Maîtriser ses coûts"],
        "outcomes_en": ["Secure an AWS account with IAM", "Deploy an application on EC2 and S3", "Design a VPC network", "Keep costs under control"],
        "prerequisites_fr": "<p>Notions de base en réseau et en ligne de commande.</p>",
        "prerequisites_en": "<p>Basic networking and command-line knowledge.</p>",
        "preparation_fr": "<ul><li>Créer un compte AWS (palier gratuit)</li><li>Installer l'AWS CLI</li></ul>",
        "preparation_en": "<ul><li>Create an AWS account (free tier)</li><li>Install the AWS CLI</li></ul>",
        "tools": ["AWS", "AWS CLI", "IAM", "EC2", "S3", "RDS"],
        "level": "beginner", "duration_weeks": 4, "language": "bilingual", "featured": False,
        "keywords": ["aws", "cloud", "infonuagique"],
        "weeks": [("Comptes et IAM", "Accounts and IAM"), ("Réseau et calcul", "Networking and compute"), ("Stockage et bases de données", "Storage and databases"), ("Architecture et coûts", "Architecture and costs")],
        "cohorts": [(30, 4, 4, "18:30", "21:00", [3], 699_00, None)],
    },
]

PAGES = {
    "about": ("À propos", "About", "<h2>Notre histoire</h2><p>[Texte à fournir par le propriétaire.]</p><h2>Notre approche pédagogique</h2><p>Des cohortes en direct, en petits groupes, avec des projets concrets.</p>", "<h2>Our story</h2><p>[Text to be provided by the owner.]</p><h2>Our teaching approach</h2><p>Live cohorts in small groups, with hands-on projects.</p>"),
    "terms": ("Conditions de vente", "Terms of sale", "<p><strong>[Gabarit à faire valider par un avocat avant le lancement.]</strong></p><p>Les prix sont en dollars canadiens et incluent tous les frais; seules la TPS et la TVQ (ou les taxes de votre province) s'ajoutent.</p>", "<p><strong>[Template to be validated by a lawyer before launch.]</strong></p><p>Prices are in Canadian dollars and include all fees; only GST and QST (or your province's taxes) are added.</p>"),
    "privacy": ("Politique de confidentialité", "Privacy policy", "<p><strong>[Gabarit à faire valider par un avocat avant le lancement.]</strong></p><p>Nous recueillons seulement les renseignements nécessaires à votre inscription. Vos données sont hébergées au Canada. Fournisseurs : Stripe (paiement), fournisseur de courriel.</p>", "<p><strong>[Template to be validated by a lawyer before launch.]</strong></p><p>We only collect the information needed for your enrollment. Your data is hosted in Canada. Providers: Stripe (payment), email provider.</p>"),
    "refunds": ("Politique d'annulation et de remboursement", "Cancellation and refund policy", "<p><strong>[Gabarit à faire valider par un avocat selon la Loi sur la protection du consommateur.]</strong></p>", "<p><strong>[Template to be validated by a lawyer under Quebec's Consumer Protection Act.]</strong></p>"),
}

BLOCKS = {
    "hero_title": ("Apprenez en direct avec des experts", "Learn live with experts"),
    "hero_subtitle": ("Des bootcamps intensifs en ligne, en petits groupes, pour développer des compétences recherchées.", "Intensive online bootcamps in small groups to build in-demand skills."),
    "how_1_title": ("Choisissez", "Choose"),
    "how_1_text": ("Comparez les programmes, les horaires et les prix.", "Compare programs, schedules and prices."),
    "how_2_title": ("Inscrivez-vous", "Enroll"),
    "how_2_text": ("Payez en un seul paiement sécurisé, même pour plusieurs bootcamps.", "Pay once, securely, even for several bootcamps."),
    "how_3_title": ("Apprenez en direct", "Learn live"),
    "how_3_text": ("Rejoignez vos séances depuis votre tableau de bord.", "Join your sessions from your dashboard."),
    "newsletter_text": ("Recevez l'annonce des prochaines cohortes.", "Hear about upcoming cohorts."),
}

FAQ = [
    ("Inscription", "Enrollment", "Puis-je m'inscrire à plusieurs bootcamps en une seule fois ?", "Can I enroll in several bootcamps at once?", "Oui. Ajoutez une cohorte de chaque bootcamp à votre panier et payez une seule fois. Un rabais s'applique à partir de deux bootcamps.", "Yes. Add one cohort of each bootcamp to your cart and pay once. A discount applies from two bootcamps."),
    ("Inscription", "Enrollment", "Que se passe-t-il si une cohorte est complète ?", "What happens if a cohort is full?", "Inscrivez-vous à la liste d'attente : si une place se libère, vous recevez un lien de paiement réservé pendant 48 heures.", "Join the waitlist: if a seat opens up, you receive a reserved payment link valid for 48 hours."),
    ("Séances", "Sessions", "Où ont lieu les séances ?", "Where do sessions take place?", "En ligne, en direct. Le lien de chaque séance est disponible dans votre tableau de bord 15 minutes avant le début.", "Online, live. Each session link is available in your dashboard 15 minutes before it starts."),
    ("Paiement", "Payment", "Quels modes de paiement acceptez-vous ?", "Which payment methods do you accept?", "Visa, Mastercard, American Express, cartes de débit acceptées par Stripe, Apple Pay et Google Pay.", "Visa, Mastercard, American Express, debit cards supported by Stripe, Apple Pay and Google Pay."),
]


def run() -> None:
    with SessionLocal() as db:
        admin = get_or_create_user(db, ADMIN_EMAIL, ADMIN_PASSWORD, "admin", "Admin", "Principal")
        instructor = get_or_create_user(
            db, INSTRUCTOR_EMAIL, INSTRUCTOR_PASSWORD, "instructor", "Camille", "Tremblay",
            title_fr="Formatrice DevOps", title_en="DevOps instructor", show_on_about=True,
            bio_fr="<p>[Biographie à fournir.]</p>", bio_en="<p>[Bio to be provided.]</p>",
        )
        cats = {
            "devops": category(db, "DevOps", "DevOps", "devops", "devops", 0),
            "cloud": category(db, "Infonuagique", "Cloud", "infonuagique", "cloud", 1),
            "prog": category(db, "Programmation", "Programming", "programmation", "programming", 2),
        }
        today = utcnow().astimezone(TZ).date()
        for pos, spec in enumerate(BOOTCAMPS):
            if db.scalar(select(Bootcamp).where(Bootcamp.slug_fr == spec["slug_fr"])):
                continue
            fields = {k: v for k, v in spec.items() if k not in ("cat", "weeks", "cohorts")}
            b = Bootcamp(**fields, category_id=cats[spec["cat"]].id, instructor_id=instructor.id, status="published", position=pos,
                         seo_title_fr=spec["title_fr"], seo_title_en=spec["title_en"], seo_description_fr=spec["summary_fr"], seo_description_en=spec["summary_en"])
            db.add(b)
            db.flush()
            for i, (wfr, wen) in enumerate(spec["weeks"], start=1):
                b.syllabus.append(SyllabusItem(week=i, position=0, title_fr=wfr, title_en=wen, content_fr=f"<p>Contenu de la semaine {i} : {wfr.lower()}.</p>", content_en=f"<p>Week {i} content: {wen.lower()}.</p>"))
            for n, (offset, _, cap, start, end, days, price, early) in enumerate(spec["cohorts"], start=1):
                start_date = next_weekday(today + timedelta(days=offset), days[0])
                end_date = start_date + timedelta(weeks=spec["duration_weeks"]) - timedelta(days=1)
                c = Cohort(
                    bootcamp_id=b.id, code=f"{spec['slug_fr'][:6].upper()}-{start_date:%Y%m}-{n}", start_date=start_date, end_date=end_date,
                    schedule=[{"weekday": d, "start": start, "end": end} for d in days], capacity=cap, price_cents=price,
                    early_bird_price_cents=early, early_bird_until=datetime.combine(start_date - timedelta(days=21), time(23, 59), tzinfo=TZ) if early else None,
                    registration_deadline=datetime.combine(start_date - timedelta(days=1), time(23, 59), tzinfo=TZ),
                    instructor_id=instructor.id, meeting_url="https://meet.example.com/" + spec["slug_en"], language=spec["language"] if spec["language"] != "bilingual" else "fr",
                )
                db.add(c)
                db.flush()
                calendar.generate_sessions(db, c, replace_future=False)
        for key, (tfr, ten, bfr, ben) in PAGES.items():
            if not db.scalar(select(Page).where(Page.key == key)):
                db.add(Page(key=key, title_fr=tfr, title_en=ten, body_fr=bfr, body_en=ben))
        for key, (vfr, ven) in BLOCKS.items():
            if not db.scalar(select(ContentBlock).where(ContentBlock.key == key)):
                db.add(ContentBlock(key=key, value_fr=vfr, value_en=ven))
        if not db.scalar(select(FaqItem).where(FaqItem.bootcamp_id.is_(None))):
            for i, (tfr, ten, qfr, qen, afr, aen) in enumerate(FAQ):
                db.add(FaqItem(theme_fr=tfr, theme_en=ten, question_fr=qfr, question_en=qen, answer_fr=afr, answer_en=aen, position=i, show_on_home=i < 3))
        site_settings.update(db, {
            "company_name": "Bootcamps Montréal", "company_address": "[Adresse à compléter], Montréal (Québec)",
            "contact_email": "info@example.com", "privacy_officer_name": "[Nom du responsable]", "privacy_officer_email": "vieprivee@example.com",
        })
        db.commit()
        print(f"Admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}  (TOTP setup required at first sign-in)")
        print(f"Instructor: {INSTRUCTOR_EMAIL} / {INSTRUCTOR_PASSWORD}")
        _ = admin


if __name__ == "__main__":
    run()
