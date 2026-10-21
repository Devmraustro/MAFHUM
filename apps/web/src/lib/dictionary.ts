export type Locale = "ar" | "fr";

export interface Dictionary {
  locale: Locale;
  dir: "rtl" | "ltr";
  site: {
    name: string;
    tagline: string;
  };
  nav: {
    home: string;
    workspace: string;
    account: string;
    auth: string;
    about: string;
    privacy: string;
    status: string;
    docs: string;
  };
  localeSwitcher: {
    label: string;
    arabic: string;
    french: string;
  };
  hero: {
    title: string;
    subtitle: string;
    status: string;
    statusDetail: string;
    readArchitecture: string;
    openWorkspace: string;
  };
  auth: {
    loginTab: string;
    registerTab: string;
    emailLabel: string;
    passwordLabel: string;
    fullNameLabel: string;
    preferredLocaleLabel: string;
    submitLogin: string;
    submitRegister: string;
    submitting: string;
    passwordHint: string;
    requestIdLabel: string;
  };
  workspace: {
    title: string;
    subtitle: string;
    signInRequiredTitle: string;
    signInRequiredBody: string;
    goToAuth: string;
    quotaLabel: string;
    newProjectTitle: string;
    projectTitleLabel: string;
    projectDescriptionLabel: string;
    createButton: string;
    emptyState: string;
    editButton: string;
    saveButton: string;
    cancelButton: string;
    deleteButton: string;
    prevPage: string;
    nextPage: string;
    updatedLabel: string;
  };
  accountPage: {
    title: string;
    profileTitle: string;
    saveProfile: string;
    savedMessage: string;
    sessionsTitle: string;
    currentSessionBadge: string;
    revokeSessionButton: string;
    logoutButton: string;
    logoutAllButton: string;
    dataLifecycleTitle: string;
    exportButton: string;
    deleteSectionTitle: string;
    deleteWarning: string;
    confirmDeleteCheckbox: string;
    deleteAccountButton: string;
  };
  about: {
    title: string;
    intro: string;
    mission: string;
  };
  principles: {
    title: string;
    items: Array<{ heading: string; body: string }>;
  };
  stack: {
    title: string;
    intro: string;
    items: Array<{ name: string; note: string }>;
  };
  status: {
    title: string;
    note: string;
    table: {
      area: string;
      state: string;
    };
    rows: Array<{ area: string; state: string }>;
  };
  integrity: {
    title: string;
    items: string[];
  };
  footer: {
    builtWith: string;
    license: string;
    budget: string;
    noPaidApis: string;
    draftNotice: string;
  };
}

const AR: Dictionary = {
  locale: "ar",
  dir: "rtl",
  site: {
    name: "MAFHUM",
    tagline: "منصة تعليمية وبحثية للطلبة — الجزائر أولًا، ثم العالم العربي.",
  },
  nav: {
    home: "الرئيسية",
    workspace: "مساحة المشاريع",
    account: "الحساب",
    auth: "الدخول",
    about: "عن المنصة",
    privacy: "الخصوصية",
    status: "حالة المشروع",
    docs: "التوثيق",
  },
  localeSwitcher: {
    label: "اللغة",
    arabic: "العربية",
    french: "français",
  },
  hero: {
    title: "MAFHUM",
    subtitle:
      "واجهة عربية أولًا باتجاه RTL حقيقي مع دعم فرنسي للمصادر والمخرجات. منصة أكاديمية في طور التأسيس.",
    status: "الحالة الحالية: تحت الإنشاء",
    statusDetail:
      "هذه المرحلة توثق فقط المستودع والمعمارية وقواعد إعادة استخدام البرمجيات مفتوحة المصدر، مع تفعيل الحسابات والجلسات الآمنة وإدارة المشاريع في المرحلة 2. لا تُعد أي ميزة مكتملة حتى تُنفَّذ وتُختبر وتُوثق في هذا المستودع.",
    readArchitecture: "اقرأ المعمارية",
    openWorkspace: "افتح مساحة المشاريع",
  },
  auth: {
    loginTab: "تسجيل الدخول",
    registerTab: "إنشاء حساب",
    emailLabel: "البريد الإلكتروني الجامعي أو الشخصي",
    passwordLabel: "كلمة المرور",
    fullNameLabel: "الاسم الكامل (اختياري)",
    preferredLocaleLabel: "اللغة المفضلة",
    submitLogin: "دخول إلى الحساب",
    submitRegister: "إنشاء حساب جديد",
    submitting: "جارٍ المعالجة...",
    passwordHint: "8 أحرف على الأقل؛ تُخزَّن مشفرة بخوارزمية bcrypt فقط دون نص صريح.",
    requestIdLabel: "معرّف الطلب",
  },
  workspace: {
    title: "مساحة المشاريع الأكاديمية",
    subtitle: "أنشئ مشاريعك الدراسية ونظّمها بعزل كامل لكل حساب وحصص استخدام واضحة على الخادم.",
    signInRequiredTitle: "يتطلب الوصول تسجيل الدخول",
    signInRequiredBody: "سجّل الدخول أو أنشئ حسابًا مجانيًا للوصول إلى مشاريعك المحفوظة.",
    goToAuth: "الانتقال إلى صفحة الدخول",
    quotaLabel: "حصة المشاريع المستخدمة",
    newProjectTitle: "مشروع جديد",
    projectTitleLabel: "عنوان المشروع",
    projectDescriptionLabel: "وصف مختصر (اختياري)",
    createButton: "إنشاء المشروع",
    emptyState: "لا توجد مشاريع محفوظة بعد. ابدأ بإنشاء مشروعك الأول أعلاه.",
    editButton: "تعديل",
    saveButton: "حفظ",
    cancelButton: "إلغاء",
    deleteButton: "حذف",
    prevPage: "السابق",
    nextPage: "التالي",
    updatedLabel: "آخر تحديث",
  },
  accountPage: {
    title: "إعدادات الحساب والجلسات",
    profileTitle: "الملف الشخصي",
    saveProfile: "حفظ التغييرات",
    savedMessage: "تم تحديث الملف الشخصي بنجاح.",
    sessionsTitle: "الجلسات النشطة",
    currentSessionBadge: "الجلسة الحالية",
    revokeSessionButton: "إبطال الجلسة",
    logoutButton: "تسجيل الخروج",
    logoutAllButton: "إبطال جميع الجلسات",
    dataLifecycleTitle: "تصدير البيانات وحذف الحساب",
    exportButton: "تنزيل نسخة JSON من بياناتي",
    deleteSectionTitle: "حذف الحساب نهائيًا",
    deleteWarning:
      "يؤدي حذف الحساب فورًا إلى إبطال جميع الجلسات وحذف سجل الحساب والمشاريع المملوكة من قاعدة البيانات الحية. تخضع النسخ الاحتياطية المشفرة والسجلات التشغيلية المنقحة لفترات الاحتفاظ التشغيلية ولا يُدَّعى حذفها بأثر رجعي.",
    confirmDeleteCheckbox: "أفهم أنه سيتم حذف حسابي ومشاريعي من قاعدة البيانات الحية ولا يمكن التراجع.",
    deleteAccountButton: "حذف الحساب نهائيًا",
  },
  about: {
    title: "عن MAFHUM",
    intro:
      "MAFHUM منصة ويب عربية أولًا تساعد الطلبة على فهم الدروس، وإعداد أبحاث أكاديمية موثقة، وصياغة ملفات TD (أعمال توجيهية) والعروض التقديمية القابلة للتعديل. الهدف أداة دراسية مجانية (ميزانية 0 دج) دون واجهات برمجة إلزامية مدفوعة، دون وعود بالاستنتاج المجاني غير المحدود دائمًا، وبدون مزاعم أو بيانات وهمية.",
    mission:
      "الاستناد إلى مصادر جزائرية رسمية وأكاديمية مسموح باستخدامها، مع روابط ومراجع يمكن التحقق منها. عدم اختراع مراجع أو أرقام صفحات أو نصوص قانونية. اعتبار أي مخرجات ذكاء اصطناعي مسودة تحتاج مراجعة الطالب. حماية الملفات الشخصية وعزل بيانات كل مستخدم.",
  },
  principles: {
    title: "المبادئ التوجيهية",
    items: [
      {
        heading: "عربية أولًا، اتجاه RTL حقيقي",
        body: "واجهة RTL حقيقية مع خصائص CSS منطقية وتعامل سليم مع النصوص المختلطة (العربية/الفرنسية/الروابط/المراجع) دون انعكاس عشوائي للشعارات أو الأيقونات.",
      },
      {
        heading: "نزاهة أكاديمية",
        body: "كل استشهاد يجب أن يحيل إلى سجل مصدر ونص داعم أو رقم صفحة/قسم عند التوفر. لا يمكن اختراع مؤلّفين أو صفحات أو روابط أو عناوين أو نصوص قانونية.",
      },
      {
        heading: "خصوصية الطلبة أولًا",
        body: "ملاك صريح لكل مشروع ومستند. عزل قوي بين المستأجرين. حذف وتصدير البيانات وفقًا لسياسة البيانات. لا سجلات تحتوي على محتوى المستندات أو الأسرار.",
      },
      {
        heading: "مجاني في المرحلة الأولى، بدون التزامات مدفوعة",
        body: "ميزانية 0 دج. لا واجهات برمجة إلزامية مدفوعة، لا دفع، ولا وعد بالاستنتاج المجاني الدائم غير المحدود. مزود الذكاء الاصطناعي تجريبي للتطوير والاختبار.",
      },
      {
        heading: "إعادة استخدام مدروسة",
        body: "مكونات مفتوحة المصدر فقط بعد مراجعة الترخيص والأمان والصيانة والاختبار، مع الاحتفاظ بإشعارات المنبع وتثبيت الإصدارات.",
      },
    ],
  },
  stack: {
    title: "البنية التقنية المقترحة",
    intro:
      "نظام موحد معياري: واجهة Next.js (App Router، TypeScript، Tailwind CSS) وواجهة برمجة FastAPI مع Pydantic v2، وقاعدة بيانات PostgreSQL مع هجرات، وبوابة ذكاء اصطناعي محايدة مع مزود تجريبي محدد للاختبار.",
    items: [
      {
        name: "Next.js + TypeScript + Tailwind CSS",
        note: "واجهة RTL حقيقية، نصوص مختلطة (عربية/فرنسية)، متجاوبة، في متناول الجميع.",
      },
      {
        name: "FastAPI + Pydantic v2",
        note: "بادئة /api/v1 لواجهات البرمجة، نقاط مراقبة /health/live و /health/ready، معرف طلب (request ID)، وغلاف أخطاء آمن.",
      },
      {
        name: "PostgreSQL مع Alembic",
        note: "هجرات قاعدة بيانات لمستخدمين متعددين؛ تطوير محلي بدون بيانات اعتماد مدفوعة.",
      },
      {
        name: "Docling (مرشح)",
        note: "مرشح لمعالجة ملفات PDF/DOCX بعد اختبار معلمي عربي/فرنسي — غير مدمج بعد.",
      },
      {
        name: "PptxGenJS (مرشح)",
        note: "مرشح للعروض التقديمية القابلة للتعديل (PPTX) بعد اختبار اتجاه RTL الفعلي — غير مدمج بعد.",
      },
      {
        name: "مزود الذكاء الاصطناعي التجريبي (mock)",
        note: "محدد للاختبار بدون بيانات اعتماد؛ المنطق يعتمد على واجهة مزود، وليس على بائع معين.",
      },
    ],
  },
  status: {
    title: "حالة المشروع",
    note:
      "يُحدّث هذا القسم مع كل مرحلة مكتملة، بناءً على نتائج اختبارات فعلية (أكواد مخرجات) تُسجل في المستودع.",
    table: {
      area: "المجال",
      state: "الحالة",
    },
    rows: [
      { area: "المستودع والتوثيق والأساس", state: "مكتمل (المرحلة 0)" },
      { area: "الهيكلة القابلة للتشغيل (واجهة + API + mock)", state: "مكتمل (المرحلة 1)" },
      { area: "الحسابات، الجلسات، المشاريع CRUD، الحصص وحذف الحساب", state: "مكتمل ومختبر (المرحلة 2)" },
      { area: "رفع المستندات والحجر الصحي ومعالج Docling", state: "غير منفذ (المرحلة 3)" },
      { area: "البحث المبني على الأدلة وحل الاستشهادات", state: "غير منفذ (المرحلة 4)" },
      { area: "المساعد التعليمي وملفات TD", state: "غير منفذ (المرحلة 5)" },
      { area: "العروض التقديمية القابلة للتعديل (PPTX)", state: "غير منفذ (المرحلة 6)" },
      { area: "الإدارة، التدقيق، والتجريب للنشر", state: "غير منفذ (المرحلة 7)" },
    ],
  },
  integrity: {
    title: "الخصوصية والنزاهة الأكاديمية",
    items: [
      "لا محتوى وهمي أو بيانات مفبركة تُمرَّر على أنها حقيقية.",
      "جميع مخرجات الذكاء الاصطناعي مسودات تتطلب مراجعة الطالب وتُوضع علامات واضحة على حالتها.",
      "الروابط إلى المصادر الأولية فقط عند السماح بالشروط؛ لا تجاوز لجدران الدفع أو المصادقة.",
      "العلامة التجارية هي MAFHUM فقط — لا شعار MAFHUM AI ولا DZ University AI في واجهات المنتج.",
    ],
  },
  footer: {
    builtWith: "بُنِي باستخدام Next.js و FastAPI — مصدر مفتوح بعد مراجعة الترخيص.",
    license: "راجع THIRD_PARTY_NOTICES.md لإشعارات الأطراف الثالثة.",
    budget: "الميزانية: 0 دج — مرحلة أولى مجانية.",
    noPaidApis: "لا واجهات برمجة إلزامية مدفوعة.",
    draftNotice: "أي مخرجات ذكاء اصطناعي مسودة — خاضعة للمراجعة.",
  },
};

const FR: Dictionary = {
  ...AR,
  locale: "fr",
  dir: "ltr",
  site: {
    name: "MAFHUM",
    tagline: "Plateforme éducative et de recherche pour les étudiants — l'Algérie d'abord, puis le monde arabe.",
  },
  nav: {
    home: "Accueil",
    workspace: "Projets",
    account: "Compte",
    auth: "Connexion",
    about: "À propos",
    privacy: "Confidentialité",
    status: "État du projet",
    docs: "Documentation",
  },
  localeSwitcher: {
    label: "Langue",
    arabic: "العربية",
    french: "français",
  },
  hero: {
    title: "MAFHUM",
    subtitle:
      "Interface arabe-first en RTL véritable avec prise en charge du français pour les sources et les sorties. Plateforme académique en cours de construction.",
    status: "État actuel : en construction",
    statusDetail:
      "Cette phase documente uniquement le dépôt, l'architecture et les règles de réutilisation des logiciels libres, avec authentification, sessions sécurisées et gestion de projets activées en Phase 2.",
    readArchitecture: "Lire l'architecture",
    openWorkspace: "Ouvrir l'espace projets",
  },
  auth: {
    loginTab: "Connexion",
    registerTab: "Créer un compte",
    emailLabel: "Adresse e-mail universitaire ou personnelle",
    passwordLabel: "Mot de passe",
    fullNameLabel: "Nom complet (facultatif)",
    preferredLocaleLabel: "Langue préférée",
    submitLogin: "Se connecter",
    submitRegister: "Créer le compte",
    submitting: "Traitement en cours...",
    passwordHint: "8 caractères minimum ; haché uniquement avec bcrypt, jamais stocké en clair.",
    requestIdLabel: "ID de requête",
  },
  workspace: {
    title: "Espace de projets académiques",
    subtitle: "Créez et gérez vos projets d'étude avec isolation stricte par compte et quotas côté serveur.",
    signInRequiredTitle: "Authentification requise",
    signInRequiredBody: "Connectez-vous ou créez un compte gratuit pour accéder à vos projets enregistrés.",
    goToAuth: "Aller à la page de connexion",
    quotaLabel: "Quota de projets utilisé",
    newProjectTitle: "Nouveau projet",
    projectTitleLabel: "Titre du projet",
    projectDescriptionLabel: "Description courte (facultatif)",
    createButton: "Créer le projet",
    emptyState: "Aucun projet enregistré pour le moment. Créez votre premier projet ci-dessus.",
    editButton: "Modifier",
    saveButton: "Enregistrer",
    cancelButton: "Annuler",
    deleteButton: "Supprimer",
    prevPage: "Précédent",
    nextPage: "Suivant",
    updatedLabel: "Mis à jour",
  },
  accountPage: {
    title: "Paramètres du compte et sessions",
    profileTitle: "Profil utilisateur",
    saveProfile: "Enregistrer les modifications",
    savedMessage: "Profil mis à jour avec succès.",
    sessionsTitle: "Sessions actives",
    currentSessionBadge: "Session actuelle",
    revokeSessionButton: "Révoquer",
    logoutButton: "Se déconnecter",
    logoutAllButton: "Révoquer toutes les sessions",
    dataLifecycleTitle: "Exportation des données et suppression du compte",
    exportButton: "Exporter mes données (JSON)",
    deleteSectionTitle: "Suppression définitive du compte",
    deleteWarning:
      "La suppression révoque immédiatement toutes les sessions et supprime le compte ainsi que les projets associés de la base principale. Les sauvegardes chiffrées et journaux opérationnels expurgés suivent leur fenêtre de rétention habituelle.",
    confirmDeleteCheckbox: "Je comprends que mon compte et mes projets seront supprimés de la base active de façon irréversible.",
    deleteAccountButton: "Supprimer définitivement le compte",
  },
  about: {
    title: "À propos de MAFHUM",
    intro:
      "MAFHUM est une plateforme web arabe-first qui aide les étudiants à comprendre leurs cours, préparer des recherches académiques sourcées, rédiger des fiches TD (travaux dirigés) et des présentations modifiables. Outil d'étude gratuit (budget 0 DZD), sans API payante obligatoire, sans promesse d'inférence gratuite illimitée permanente, et sans fausses données.",
    mission:
      "S'appuyer sur des sources officielles et académiques algériennes autorisées, avec liens et références vérifiables ; ne jamais inventer de références, numéros de page ou textes légaux ; considérer toute production d'IA comme un brouillon nécessitant une révision étudiante ; protéger les données personnelles et isoler les données de chaque utilisateur.",
  },
  principles: {
    title: "Principes directeurs",
    items: [
      {
        heading: "Arabe d'abord, RTL véritable",
        body: "Interface RTL véritable, propriétés CSS logiques, gestion correcte des scripts mixtes (arabe/français/URLs/références), sans miroir systématique des logos ou icônes.",
      },
      {
        heading: "Intégrité académique",
        body: "Chaque citation doit résoudre vers un enregistrement de source et un extrait ou une page/section quand disponible. Pas d'invention d'auteurs, pages, URLs, titres ou dispositions légales.",
      },
      {
        heading: "Confidentialité des étudiants d'abord",
        body: "Propriété explicite de chaque projet et document ; isolation forte entre locataires ; export et suppression des données selon la politique des données ; aucun contenu de document ni secret dans les logs ordinaires.",
      },
      {
        heading: "Gratuit en phase MVP, aucun engagement payant",
        body: "Budget 0 DZD. Aucune API payante obligatoire, aucun paiement, et aucune promesse d'inférence gratuite permanente illimitée. Fournisseur IA fictif pour le développement et les tests.",
      },
      {
        heading: "Réutilisation de logiciels libres étudiée",
        body: "Composants open-source uniquement après examen de la licence, de la sécurité, de la maintenance et des tests ; conservation des avis amont et épinglage des versions.",
      },
    ],
  },
  stack: {
    title: "Stack technique proposée",
    intro:
      "Monolithe modulaire : interface Next.js (App Router, TypeScript, Tailwind), API FastAPI avec Pydantic v2, base de données PostgreSQL avec migrations, passerelle IA neutre avec fournisseur fictif déterministe pour les tests.",
    items: [
      {
        name: "Next.js + TypeScript + Tailwind CSS",
        note: "Interface RTL véritable, scripts mixtes arabe/français, responsive, accessible.",
      },
      {
        name: "FastAPI + Pydantic v2",
        note: "Contrat API typé sur /api/v1, points de contrôle /health/live et /health/ready, ID de requête et enveloppe d'erreurs sûre.",
      },
      {
        name: "PostgreSQL avec Alembic",
        note: "Migrations de base de données multi-utilisateurs ; développement local sans identifiants payants.",
      },
      {
        name: "Docling (candidat)",
        note: "Candidat pour l'analyse PDF/DOCX après un benchmark arabe/français — non intégré.",
      },
      {
        name: "PptxGenJS (candidat)",
        note: "Candidat pour les présentations PPTX modifiables après validation RTL réelle — non intégré.",
      },
      {
        name: "Fournisseur IA fictif (mock)",
        note: "Déterministe pour les tests sans identifiants ; la logique dépend d'une interface de fournisseur, pas d'un vendeur précis.",
      },
    ],
  },
  status: {
    title: "État du projet",
    note: "Cette section est mise à jour à chaque phase achevée, sur la base de résultats de tests réels (codes de sortie) enregistrés dans le dépôt.",
    table: {
      area: "Domaine",
      state: "État",
    },
    rows: [
      { area: "Dépôt, documentation et base", state: "Terminé (Phase 0)" },
      { area: "Squelette exécutable (interface + API + mock)", state: "Terminé (Phase 1)" },
      { area: "Comptes, sessions, projets CRUD, quotas et suppression", state: "Terminé et vérifié (Phase 2)" },
      { area: "Envoi de documents, mise en quarantaine et adaptateur Docling", state: "Non implémenté (Phase 3)" },
      { area: "Recherche fondée sur des preuves et résolution des citations", state: "Non implémenté (Phase 4)" },
      { area: "Assistant pédagogique et fiches TD", state: "Non implémenté (Phase 5)" },
      { area: "Présentations modifiables (PPTX)", state: "Non implémenté (Phase 6)" },
      { area: "Administration, audit et répétition de déploiement", state: "Non implémenté (Phase 7)" },
    ],
  },
  integrity: {
    title: "Confidentialité et intégrité académique",
    items: [
      "Aucune donnée fictive ou fabriquée présentée comme réelle.",
      "Toute production d'IA est un brouillon nécessitant une révision étudiante, clairement identifié comme tel.",
      "Liens vers les sources primaires uniquement lorsque les conditions le permettent ; aucun contournement de paywall ou d'authentification.",
      "La marque produit est MAFHUM uniquement — pas de « MAFHUM AI » ni de « DZ University AI » dans les interfaces.",
    ],
  },
  footer: {
    builtWith: "Construit avec Next.js et FastAPI — open-source après examen des licences.",
    license: "Consulter THIRD_PARTY_NOTICES.md pour les avis de tiers.",
    budget: "Budget : 0 DZD — phase MVP gratuite.",
    noPaidApis: "Aucune API payante obligatoire.",
    draftNotice: "Toute production d'IA est un brouillon — à réviser.",
  },
};

export const DICTIONARIES: Record<Locale, Dictionary> = { ar: AR, fr: FR };

export function getDictionary(locale: Locale): Dictionary {
  return DICTIONARIES[locale];
}

export function validateDictionary(locale: Locale): string[] {
  const d = DICTIONARIES[locale];
  const problems: string[] = [];
  const walk = (value: unknown, path: string): void => {
    if (value === null || value === undefined) {
      problems.push(`${path} est vide / is empty`);
      return;
    }
    if (typeof value === "string") {
      if (!value.trim()) problems.push(`${path} is an empty string`);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, i) => walk(item, `${path}[${i}]`));
      return;
    }
    if (typeof value === "object") {
      for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
        walk(val, `${path}.${key}`);
      }
    }
  };
  walk(d, d.locale);
  return problems;
}
