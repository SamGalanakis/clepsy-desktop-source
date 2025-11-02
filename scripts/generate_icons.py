from PIL import Image
from pathlib import Path


def generate_icons(source_image_path: str | Path, output_dir: str | Path):
    """
    Generates .ico and .icns files from a source PNG image.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_image_path = Path(source_image_path)
    base_name = source_image_path.stem

    # --- Generate .ico for Windows ---
    ico_output_path = output_dir / f"{base_name}.ico"
    img = Image.open(str(source_image_path))
    icon_sizes = [
        (16, 16),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    ]
    img.save(ico_output_path, "ICO", sizes=icon_sizes)
    print(f"Generated Windows icon: {ico_output_path}")

    # --- Generate .icns for macOS ---
    icns_output_path = output_dir / f"{base_name}.icns"
    img = Image.open(str(source_image_path))

    # For .icns, Pillow automatically creates the multi-resolution icon from a single high-res image.
    # It's generally best to provide a 1024x1024 source image for this.
    img.save(icns_output_path, "ICNS")
    print(f"Generated macOS icon: {icns_output_path}")


if __name__ == "__main__":
    source_image = Path("media/logo.png")
    output_directory = Path("media/dist")
    generate_icons(source_image, output_directory)
