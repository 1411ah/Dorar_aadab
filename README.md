# موسوعة الآداب الشرعية — تصدير

سكريبتات لاستكشاف وتصدير [موسوعة الآداب الشرعية](https://dorar.net/aadab) من موقع درر السنية إلى EPUB و Markdown.

## الملفات

| الملف | الوصف |
|-------|-------|
| `explore_aadab.py` | استكشاف بنية الموقع وتحليلها |
| `requirements.txt` | المكتبات المطلوبة |

## التشغيل على GitHub Actions

1. تبويب **Actions**
2. اختر **Explore dorar/aadab**
3. اضغط **Run workflow**
4. بعد الانتهاء: قسم **Artifacts** ← حمّل النتائج

## التشغيل المحلي

```bash
pip install -r requirements.txt

# عيّنة 30 صفحة
SAMPLE=30 python explore_aadab.py

# كامل
python explore_aadab.py
```

## المخرجات

- `exploration_report.txt` — تقرير مفصّل عن بنية الموقع
- `sampled_pages.json` — بيانات الصفحات المُستكشَفة
