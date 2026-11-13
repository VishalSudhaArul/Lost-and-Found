"""Copy existing uploaded media from the local disk to Cloudinary under the same names.

    python manage.py upload_media_to_cloud            # dry run: lists what would be uploaded
    python manage.py upload_media_to_cloud --apply    # zip backup of media/ + private_media/, then upload and verify

Needs CLOUDINARY_URL in the environment for this run (scripts/upload_media_to_cloudinary.ps1 takes it
from the clipboard without showing it). Public item photos go to normal Cloudinary delivery; private
evidence goes to "authenticated" storage. Local files are only read, never changed or deleted. Every
upload is verified by downloading it again and comparing SHA-256. Output shows paths and counts only.
"""
import hashlib
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from lostfound.models import Claim, DisposalRecord, Handover, Item
from lostfound.services.cloud_storage import PrivateCloudinaryStorage, PublicCloudinaryStorage, cloudinary_enabled

FIELDS = [  # (model, field, private?)
    (Item, "photo", False),
    (Claim, "attachment", True),
    (Handover, "signature_image", True),
    (Handover, "proof_photo", True),
    (DisposalRecord, "evidence", True),
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Command(BaseCommand):
    help = "Upload existing local media to Cloudinary with the same names (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--backup-dir", default=str(Path(settings.BASE_DIR) / "backups"))

    def handle(self, *args, **opts):
        if not cloudinary_enabled():
            raise CommandError("CLOUDINARY_URL is not set for this run; nothing was uploaded.")
        public, private = PublicCloudinaryStorage(), PrivateCloudinaryStorage()
        roots = {False: Path(settings.MEDIA_ROOT), True: Path(settings.PRIVATE_MEDIA_ROOT)}

        jobs, missing = [], []
        for model, field, is_private in FIELDS:
            for name in (model._base_manager.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True})
                         .values_list(field, flat=True)):
                local = roots[is_private] / name
                (jobs if local.is_file() else missing).append((model.__name__, field, name, is_private, local))

        self.stdout.write(f"Mode: {'APPLY' if opts['apply'] else 'DRY RUN'}")
        self.stdout.write(f"Files referenced by the database: {len(jobs) + len(missing)} "
                          f"({len(jobs)} found locally, {len(missing)} missing locally)")
        for m, f, name, is_private, _ in missing:
            self.stdout.write(self.style.WARNING(f"  missing locally (cannot be restored): {m}.{f} {name}"))
        for m, f, name, is_private, _ in jobs:
            self.stdout.write(f"  {'private' if is_private else 'public '} {m}.{f} {name}")
        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to back up and upload."))
            return

        backup = Path(opts["backup_dir"]) / f"media-before-cloud-{datetime.now():%Y%m%d-%H%M%S}.zip"
        backup.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
            for root_flag, root in roots.items():
                if root.is_dir():
                    for p in root.rglob("*"):
                        if p.is_file():
                            z.write(p, Path("private_media" if root_flag else "media") / p.relative_to(root))
        self.stdout.write(f"Backup of local media: {backup}")

        uploaded = skipped = failed = 0
        for m, f, name, is_private, local in jobs:
            storage = private if is_private else public
            data = local.read_bytes()
            try:
                if storage.exists(name):
                    skipped += 1
                    state = "already in Cloudinary"
                else:
                    with local.open("rb") as fh:
                        storage.save(name, File(fh, name=local.name))
                    uploaded += 1
                    state = "uploaded"
                with storage.open(name) as remote:
                    ok = sha256(remote.read()) == sha256(data)
            except Exception as exc:  # report and continue; nothing local is touched
                ok, state = False, f"error ({type(exc).__name__})"
            failed += not ok
            self.stdout.write(f"  {'OK  ' if ok else 'FAIL'} {name}: {state}{'' if ok else ' - content differs'}")

        self.stdout.write(f"Uploaded {uploaded}, already present {skipped}, failed {failed}. Local files unchanged.")
        if failed:
            raise CommandError(f"{failed} file(s) did not verify.")
