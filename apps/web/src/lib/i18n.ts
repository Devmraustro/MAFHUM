/**
 * Interface strings for MAFHUM.
 *
 * Arabic is the primary language and the default. French is provided for the
 * secondary locale. Every user-facing string lives here so the two locales cannot
 * drift apart silently, and so no hardcoded Arabic is scattered through components.
 */

export type Locale = "ar" | "fr";

export const DEFAULT_LOCALE: Locale = "ar";

type Dictionary = Record<string, string>;

const ar: Dictionary = {
  "app.name": "مَفْهُوم",
  "app.tagline": "منصة تعليمية وبحثية للطلبة",
  "nav.documents": "المستندات",
  "nav.projects": "المشاريع",
  "nav.signout": "تسجيل الخروج",
  "auth.signin": "تسجيل الدخول",
  "auth.signup": "إنشاء حساب",
  "auth.email": "البريد الإلكتروني",
  "auth.password": "كلمة المرور",
  "auth.displayName": "الاسم الظاهر",
  "auth.noAccount": "ليس لديك حساب؟",
  "auth.haveAccount": "لديك حساب بالفعل؟",
  "documents.title": "مستنداتي",
  "documents.upload": "رفع مستند",
  "documents.uploadHint": "الصيغ المدعومة: PDF و DOCX.",
  "documents.selectFile": "اختر ملفًا",
  "documents.attachToProject": "ربط بمشروع (اختياري)",
  "documents.noProject": "بدون مشروع",
  "documents.empty": "لا توجد مستندات بعد. ارفع ملف PDF أو DOCX للبدء.",
  "documents.name": "الاسم",
  "documents.format": "الصيغة",
  "documents.size": "الحجم",
  "documents.status": "الحالة",
  "documents.pages": "الصفحات",
  "documents.open": "فتح",
  "documents.retry": "إعادة المحاولة",
  "documents.delete": "حذف",
  "documents.confirmDelete": "سيُحذف المستند ونصه المستخرج نهائيًا. هل تريد المتابعة؟",
  "documents.downloading": "تنزيل",
  "documents.uploading": "جارٍ الرفع…",
  "documents.processing": "جارٍ المعالجة…",
  "documents.refresh": "تحديث",
  "status.pending": "في الانتظار",
  "status.processing": "قيد المعالجة",
  "status.ready": "جاهز",
  "status.failed": "فشل",
  "detail.title": "تفاصيل المستند",
  "detail.extractedText": "النص المستخرج",
  "detail.noTextYet": "لم يتم استخراج النص بعد.",
  "detail.metadata": "البيانات الوصفية",
  "detail.page": "الصفحة",
  "detail.scanned": "يبدو أن هذا الملف نسخة مصوّرة بدون طبقة نصية. خدمة التعرف الضوئي على الحروف (OCR) غير متاحة حاليًا، لذلك لا يمكن استخراج النص.",
  "documents.noMalwareScanning": "لا يتوفر فحص للفيروسات في مفهم. يتم التحقق من بنية الملف وامتداده وحجمه فقط، ولا يُفحص محتواه بحثًا عن برامج خبيثة.",
  "nav.research": "البحث",
  "research.title": "البحث المبني على الأدلة",
  "research.subtitle": "تُبنى الإجابة حصريًا على مقاطع من مستنداتك، مع ذكر مصدر كل مقطع. لا يُختلق أي مرجع.",
  "research.questionLabel": "سؤالك",
  "research.placeholder": "مثال: ما هو القانون 08-09 لحماية المعطيات الشخصية؟",
  "research.ask": "ابحث",
  "research.grounded": "إجابة مدعومة بالأدلة",
  "research.insufficient": "الأدلة غير كافية",
  "research.noEvidence": "لا تتوفر مصادر",
  "research.citations": "المقاطع المستشهد بها",
  "research.noCitations": "لا توجد مقاطع مطابقة.",
  "research.error": "تعذّر إتمام البحث. حاول مرة أخرى.",
  "research.sourcesTitle": "المصادر الأكاديمية المعروفة",
  "research.retrievalDisabled": "الجلب من المصادر الخارجية غير مفعّل. لم يتم استرجاع أي مصدر فعليًا.",
  "research.retrievalEnabled": "الجلب من المصادر الخارجية مفعّل.",
  "research.unverified": "غير مُتحقَّق منه",
  "research.noUrl": "الرابط غير مؤكد",
  "detail.back": "رجوع",
  "detail.loadMore": "تحميل المزيد",
  "detail.text": "النص المستخرج",
  "detail.characters": "عدد الأحرف",
  "detail.segments": "مقطعًا",
  "detail.language": "لغة النص",
  "detail.uploadedAt": "تاريخ الرفع",
  "detail.processedAt": "تاريخ المعالجة",
  "detail.parser": "المحلّل المستخدم",
  "detail.project": "المشروع",
  "detail.errorCode": "رمز الخطأ",
  "detail.errorMessage": "سبب الفشل",
  "detail.download": "تنزيل الملف الأصلي",
  "detail.processing": "المستند قيد المعالجة. تُحدَّث هذه الصفحة تلقائيًا عند الانتهاء.",
  "detail.failed": "فشلت معالجة هذا المستند. يمكنك محاولة المعالجة مجددًا أو حذف الملف.",
  "detail.empty": "تمت معالجة المستند لكن لم يُستخرج أي نص منه.",
  "detail.pageUnknown": "بدون رقم صفحة",
  "detail.noSegments": "لا توجد مقاطع نصية لعرضها.",
  "a11y.documentsTable": "قائمة المستندات المرفوعة",
  "a11y.statusAnnouncer": "عدد المستندات",
  "quota.title": "الاستخدام",
  "quota.documents": "عدد المستندات",
  "quota.storage": "مساحة التخزين",
  "error.generic": "حدث خطأ غير متوقع. حاول مجددًا.",
  "error.network": "تعذّر الاتصال بالخادم. تحقق من الاتصال ثم حاول مجددًا.",
  "error.sessionExpired": "انتهت الجلسة. سجّل الدخول مجددًا.",
  "common.loading": "جارٍ التحميل…",
  "common.cancel": "إلغاء",
  "common.confirm": "تأكيد",
  "common.close": "إغلاق",
  "common.optional": "اختياري",
  "common.bytes": "بايت",
  "a11y.skipToContent": "تخطَّ إلى المحتوى",
  "login.title": "تسجيل الدخول إلى مَفْهُوم",
  "login.subtitle": "ارفع مستنداتك وادرسها بأمان.",
};

const fr: Dictionary = {
  "app.name": "MAFHUM",
  "app.tagline": "Plateforme éducative et de recherche pour les étudiants",
  "nav.documents": "Documents",
  "nav.projects": "Projets",
  "nav.signout": "Se déconnecter",
  "auth.signin": "Se connecter",
  "auth.signup": "Créer un compte",
  "auth.email": "Adresse e-mail",
  "auth.password": "Mot de passe",
  "auth.displayName": "Nom affiché",
  "auth.noAccount": "Vous n'avez pas de compte ?",
  "auth.haveAccount": "Vous avez déjà un compte ?",
  "documents.title": "Mes documents",
  "documents.upload": "Téléverser un document",
  "documents.uploadHint": "Formats pris en charge : PDF et DOCX.",
  "documents.selectFile": "Choisir un fichier",
  "documents.attachToProject": "Associer à un projet (facultatif)",
  "documents.noProject": "Aucun projet",
  "documents.empty": "Aucun document pour le moment. Téléversez un fichier PDF ou DOCX pour commencer.",
  "documents.name": "Nom",
  "documents.format": "Format",
  "documents.size": "Taille",
  "documents.status": "État",
  "documents.pages": "Pages",
  "documents.open": "Ouvrir",
  "documents.retry": "Réessayer",
  "documents.delete": "Supprimer",
  "documents.confirmDelete": "Le document et son texte extrait seront définitivement supprimés. Continuer ?",
  "documents.downloading": "Téléchargement",
  "documents.uploading": "Téléversement en cours…",
  "documents.processing": "Traitement en cours…",
  "documents.refresh": "Actualiser",
  "status.pending": "En attente",
  "status.processing": "En traitement",
  "status.ready": "Prêt",
  "status.failed": "Échec",
  "detail.title": "Détails du document",
  "detail.extractedText": "Texte extrait",
  "detail.noTextYet": "Le texte n'a pas encore été extrait.",
  "detail.metadata": "Métadonnées",
  "detail.page": "Page",
  "detail.scanned": "Ce fichier semble être une numérisation sans couche texte. La reconnaissance optique de caractères (OCR) n'est pas disponible actuellement.",
  "documents.noMalwareScanning": "MAFHUM ne dispose d'aucun antivirus. Seuls la structure, l'extension et la taille du fichier sont vérifiés ; son contenu n'est pas analysé.",
  "nav.research": "Recherche",
  "research.title": "Recherche fondée sur les preuves",
  "research.subtitle": "La réponse repose uniquement sur des passages de vos documents, chacun accompagné de sa source. Aucune référence n'est inventée.",
  "research.questionLabel": "Votre question",
  "research.placeholder": "Exemple : que prévoit la loi 08-09 sur la protection des données ?",
  "research.ask": "Rechercher",
  "research.grounded": "Réponse étayée",
  "research.insufficient": "Preuves insuffisantes",
  "research.noEvidence": "Aucune source disponible",
  "research.citations": "Passages cités",
  "research.noCitations": "Aucun passage correspondant.",
  "research.error": "La recherche a échoué. Veuillez réessayer.",
  "research.sourcesTitle": "Sources académiques connues",
  "research.retrievalDisabled": "La récupération externe est désactivée. Aucune source n'a été réellement consultée.",
  "research.retrievalEnabled": "La récupération externe est activée.",
  "research.unverified": "Non vérifiée",
  "research.noUrl": "Adresse non confirmée",
  "detail.back": "Retour",
  "detail.loadMore": "Charger plus",
  "detail.text": "Texte extrait",
  "detail.characters": "Nombre de caractères",
  "detail.segments": "segments",
  "detail.language": "Langue du texte",
  "detail.uploadedAt": "Date de téléversement",
  "detail.processedAt": "Date de traitement",
  "detail.parser": "Analyseur utilisé",
  "detail.project": "Projet",
  "detail.errorCode": "Code d'erreur",
  "detail.errorMessage": "Raison de l'échec",
  "detail.download": "Télécharger le fichier original",
  "detail.processing": "Le document est en cours de traitement. Cette page se met à jour automatiquement.",
  "detail.failed": "Le traitement de ce document a échoué. Vous pouvez réessayer ou supprimer le fichier.",
  "detail.empty": "Le document a été traité mais aucun texte n'en a été extrait.",
  "detail.pageUnknown": "Sans numéro de page",
  "detail.noSegments": "Aucun segment de texte à afficher.",
  "a11y.documentsTable": "Liste des documents téléversés",
  "a11y.statusAnnouncer": "Nombre de documents",
  "quota.title": "Utilisation",
  "quota.documents": "Nombre de documents",
  "quota.storage": "Espace de stockage",
  "error.generic": "Une erreur inattendue est survenue. Veuillez réessayer.",
  "error.network": "Impossible de joindre le serveur. Vérifiez la connexion.",
  "error.sessionExpired": "Session expirée. Veuillez vous reconnecter.",
  "common.loading": "Chargement…",
  "common.cancel": "Annuler",
  "common.confirm": "Confirmer",
  "common.close": "Fermer",
  "common.optional": "facultatif",
  "common.bytes": "octets",
  "a11y.skipToContent": "Aller au contenu",
  "login.title": "Connexion à MAFHUM",
  "login.subtitle": "Téléversez et étudiez vos documents en toute sécurité.",
};

const dictionaries: Record<Locale, Dictionary> = { ar, fr };

/** Look up a localized string. Falls back to Arabic, then to the key itself. */
export function t(locale: Locale, key: string): string {
  return dictionaries[locale][key] ?? dictionaries.ar[key] ?? key;
}

export function makeTranslator(locale: Locale) {
  return (key: string): string => t(locale, key);
}

/** Text direction for a locale. Arabic is right-to-left. */
export function directionOf(locale: Locale): "rtl" | "ltr" {
  return locale === "ar" ? "rtl" : "ltr";
}

/**
 * Format a byte count for display. Uses locale-aware grouping without inventing
 * precision the server did not provide.
 */
export function formatBytes(bytes: number, locale: Locale): string {
  const nf = new Intl.NumberFormat(locale === "ar" ? "ar-DZ" : "fr-FR");
  if (bytes < 1024) return `${nf.format(bytes)} ${t(locale, "common.bytes")}`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${nf.format(Math.round(kb))} KB`;
  const mb = kb / 1024;
  return `${nf.format(Math.round(mb * 10) / 10)} MB`;
}
