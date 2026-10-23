from django.db import migrations


def add_lenses(apps, schema_editor):
    """Boards that already have a library get the lenses added since."""
    from board.library import later

    Lens = apps.get_model("board", "Lens")
    Workspace = apps.get_model("accounts", "Workspace")
    for workspace in Workspace.objects.filter(lenses__isnull=False).distinct():
        have = set(Lens.objects.filter(workspace=workspace).values_list("name", flat=True))
        Lens.objects.bulk_create([
            Lens(workspace=workspace, name=name, source=source, question=question)
            for name, source, question in later() if name not in have
        ])


class Migration(migrations.Migration):
    dependencies = [("board", "0007_notice"), ("accounts", "0005_user_email_digest_user_last_digest_at")]

    operations = [migrations.RunPython(add_lenses, migrations.RunPython.noop)]
