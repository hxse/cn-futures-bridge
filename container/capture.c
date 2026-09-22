/* 仅读取 X11 根窗口；不调整窗口、焦点或输入状态。 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <png.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static int channel_shift(unsigned long mask)
{
    int shift = 0;
    if (!mask) return -1;
    while (!(mask & 1)) { mask >>= 1; ++shift; }
    return mask == 255 ? shift : -1;
}

static int save_png(XImage *image, const char *path)
{
    int red = channel_shift(image->red_mask);
    int green = channel_shift(image->green_mask);
    int blue = channel_shift(image->blue_mask);
    if (red < 0 || green < 0 || blue < 0 || image->width <= 0 || image->height <= 0
        || (size_t)image->width > SIZE_MAX / 3) return 1;
    png_bytep row = malloc((size_t)image->width * 3);
    if (!row) return 1;
    FILE *file = fopen(path, "wb");
    if (!file) { free(row); return 1; }
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    png_infop info = png ? png_create_info_struct(png) : NULL;
    int result = 1;
    if (!png || !info || setjmp(png_jmpbuf(png))) goto done;
    png_init_io(png, file);
    /* 截图是短暂诊断工件，使用快速的无损压缩。 */
    png_set_compression_level(png, 1);
    png_set_IHDR(png, info, image->width, image->height, 8, PNG_COLOR_TYPE_RGB,
                 PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);
    for (int y = 0; y < image->height; ++y) {
        for (int x = 0; x < image->width; ++x) {
            unsigned long pixel = XGetPixel(image, x, y);
            size_t offset = (size_t)x * 3;
            row[offset] = (pixel >> red) & 255;
            row[offset + 1] = (pixel >> green) & 255;
            row[offset + 2] = (pixel >> blue) & 255;
        }
        png_write_row(png, row);
    }
    png_write_end(png, info);
    result = 0;
done:
    png_destroy_write_struct(&png, &info);
    if (fclose(file)) result = 1;
    free(row);
    if (result) unlink(path);
    return result;
}

int main(int argc, char **argv)
{
    if (argc != 2) { fputs("usage: cfb-capture OUTPUT.png\n", stderr); return 2; }
    Display *display = XOpenDisplay(NULL);
    if (!display) { fputs("X11 display unavailable\n", stderr); return 1; }
    Window root = DefaultRootWindow(display);
    XWindowAttributes attrs;
    XImage *image = NULL;
    if (XGetWindowAttributes(display, root, &attrs) && attrs.visual->class == TrueColor)
        image = XGetImage(display, root, 0, 0, attrs.width, attrs.height, AllPlanes, ZPixmap);
    int result = image ? save_png(image, argv[1]) : 1;
    if (image) XDestroyImage(image);
    XCloseDisplay(display);
    if (result) fputs("PNG capture failed\n", stderr);
    return result;
}
