/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * Modified in 2026 by Juuso Karjanlahti: added the configurable product image format (PNG/JPEG) and JPEG
 * quality.
 */
package tools.descartes.teastore.image;

import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.Base64;
import java.util.Locale;

import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;
import javax.imageio.ImageWriter;
import javax.imageio.stream.ImageOutputStream;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import tools.descartes.teastore.entities.ImageSize;
import tools.descartes.teastore.image.cache.entry.ICachable;

/**
 * Entity for images provided by the image-provider service.
 * 
 * @author Norbert Schmitt
 *
 */
public class StoreImage implements ICachable<StoreImage> {

  /**
   * Standard image format for storage (reading and writing).
   */
  public static final String STORE_IMAGE_FORMAT = "png";

  /**
   * String prepended in the
   * 
   * <pre>
   * src
   * </pre>
   * 
   * attribute of an image tag for embedding the image data.
   */
  public static final String STORE_IMAGE_DATA_STRING = "data:image/" + STORE_IMAGE_FORMAT
      + ";base64,";

  /**
   * System property selecting the format product images are generated and scaled in: {@code png}, TeaStore's
   * original and the default, or {@code jpeg}. Web interface images always keep {@link #STORE_IMAGE_FORMAT}: they
   * are graphics, some with transparency, rather than photographs. Read when an image is encoded, which for
   * product images happens at startup and on the first request for a new size.
   */
  public static final String PRODUCT_IMAGE_FORMAT_PROPERTY = "image.productFormat";

  /**
   * System property setting the JPEG quality, above 0 and at most 1, used when product images are JPEG.
   */
  public static final String JPEG_QUALITY_PROPERTY = "image.jpegQuality";

  /**
   * JPEG quality used if {@link #JPEG_QUALITY_PROPERTY} is unset or invalid.
   */
  public static final float DEFAULT_JPEG_QUALITY = 0.8f;

  /**
   * Format name of JPEG images, as returned by {@link #productImageFormat()}.
   */
  public static final String JPEG_FORMAT = "jpeg";

  private static final String JPEG_DATA_STRING = "data:image/" + JPEG_FORMAT + ";base64,";

  // Base64 of the JPEG start-of-image marker FF D8 FF, which every JPEG begins with.
  private static final String JPEG_BASE64_PREFIX = "/9j/";

  private final long id;
  private byte[] data;
  private ImageSize size;
  private Logger log = LoggerFactory.getLogger(StoreImage.class);

  /**
   * Creates a new store image with a given id and size. The image is converted
   * from the Java internal representation to a base64 encoded byte array.
   * 
   * @param id
   *          The unique image id.
   * @param image
   *          The image data itself.
   * @param size
   *          The image size.
   */
  public StoreImage(final long id, BufferedImage image, ImageSize size) {
    this(id, image, size, STORE_IMAGE_FORMAT);
  }

  /**
   * Creates a new store image with a given id, size and format. The image is converted from the Java internal
   * representation to a base64 encoded byte array in that format.
   *
   * @param id
   *          The unique image id.
   * @param image
   *          The image data itself.
   * @param size
   *          The image size.
   * @param format
   *          Image format name, {@link #STORE_IMAGE_FORMAT} or {@link #JPEG_FORMAT}.
   */
  public StoreImage(final long id, BufferedImage image, ImageSize size, String format) {
    if (image == null) {
      log.error("Supplied image is null.");
      throw new NullPointerException("Supplied image is null.");
    }

    this.id = id;
    data = encode(image, format);
    setImageSize(size);
  }

  /**
   * Constructor.
   * @param id imageId
   * @param base64 base64 encoded byte array
   * @param size Imagesize object
   */
  public StoreImage(final long id, byte[] base64, ImageSize size) {
    if (base64 == null) {
      log.error("Supplied base64 encoded byte array is null.");
      throw new NullPointerException("Supplied base64 encoded byte array is null.");
    }

    this.id = id;
    data = Arrays.copyOf(base64, base64.length);
    setImageSize(size);
  }

  private void setImageSize(ImageSize size) {
    if (size == null) {
      log.error("Supplied image size is null.");
      throw new NullPointerException("Supplied image size is null.");
    }
    this.size = size;
  }

  /**
   * Copy constructor for StoreImage.
   * 
   * @param image
   *          Image to copy.
   */
  public StoreImage(StoreImage image) {
    if (image == null) {
      log.error("Supplied store image is null.");
      throw new NullPointerException("Store image is null.");
    }

    this.id = image.getId();
    this.data = Arrays.copyOf(image.getByteArray(), image.getByteArray().length);
    this.size = image.getSize();
  }

  /**
   * Returns the unique image identifier.
   * 
   * @return Unique image id.
   */
  public long getId() {
    return id;
  }

  /**
   * Converts the Java internal image representation to a byte array in the given format and encodes it in base64
   * for embedding.
   *
   * @param image
   *          The image to encode.
   * @param format
   *          Image format name, {@link #STORE_IMAGE_FORMAT} or {@link #JPEG_FORMAT}.
   * @return The base64 encoded image.
   * @throws IllegalArgumentException
   *           If no image writer can write the image in that format; an empty image would otherwise be stored
   *           and served without any error.
   */
  public static byte[] encode(BufferedImage image, String format) {
    ByteArrayOutputStream stream = new ByteArrayOutputStream();
    try {
      if (JPEG_FORMAT.equals(format)) {
        writeJpeg(image, stream);
      } else if (!ImageIO.write(image, format, stream)) {
        throw new IllegalArgumentException("No image writer for format " + format + ".");
      }
    } catch (IOException ioException) {
      LoggerFactory.getLogger(StoreImage.class)
          .warn("An IOException occured while trying to write image to a stream.", ioException);
    }
    return Base64.getEncoder().encode(stream.toByteArray());
  }

  // JPEG has no alpha channel, and ImageIO's JPEG writer rejects images that have one; the scaler produces
  // translucent images. They are flattened onto white, as a browser shows them on a white page.
  private static void writeJpeg(BufferedImage image, ByteArrayOutputStream stream) throws IOException {
    BufferedImage rgb = new BufferedImage(image.getWidth(), image.getHeight(), BufferedImage.TYPE_INT_RGB);
    Graphics2D graphics = rgb.createGraphics();
    try {
      graphics.drawImage(image, 0, 0, Color.WHITE, null);
    } finally {
      graphics.dispose();
    }
    ImageWriter writer = ImageIO.getImageWritersByFormatName(JPEG_FORMAT).next();
    ImageWriteParam param = writer.getDefaultWriteParam();
    param.setCompressionMode(ImageWriteParam.MODE_EXPLICIT);
    param.setCompressionQuality(jpegQuality());
    try (ImageOutputStream output = ImageIO.createImageOutputStream(stream)) {
      writer.setOutput(output);
      writer.write(null, new IIOImage(rgb, null, null), param);
    } finally {
      writer.dispose();
    }
  }

  /**
   * Returns the format product images are encoded in, from {@link #PRODUCT_IMAGE_FORMAT_PROPERTY}.
   *
   * @return {@link #JPEG_FORMAT} if the property is {@code jpeg} or {@code jpg}, otherwise
   *         {@link #STORE_IMAGE_FORMAT}.
   */
  public static String productImageFormat() {
    String value = System.getProperty(PRODUCT_IMAGE_FORMAT_PROPERTY, STORE_IMAGE_FORMAT).trim()
        .toLowerCase(Locale.ROOT);
    if (JPEG_FORMAT.equals(value) || "jpg".equals(value)) {
      return JPEG_FORMAT;
    }
    if (!STORE_IMAGE_FORMAT.equals(value)) {
      LoggerFactory.getLogger(StoreImage.class).warn("Ignoring unknown " + PRODUCT_IMAGE_FORMAT_PROPERTY
          + " value '" + value + "'. Using " + STORE_IMAGE_FORMAT + ".");
    }
    return STORE_IMAGE_FORMAT;
  }

  /**
   * Returns the JPEG quality from {@link #JPEG_QUALITY_PROPERTY}.
   *
   * @return The configured quality, or {@link #DEFAULT_JPEG_QUALITY} if the property is unset, not a number or
   *         outside (0, 1].
   */
  public static float jpegQuality() {
    String value = System.getProperty(JPEG_QUALITY_PROPERTY);
    if (value == null) {
      return DEFAULT_JPEG_QUALITY;
    }
    float quality;
    try {
      quality = Float.parseFloat(value.trim());
    } catch (NumberFormatException e) {
      quality = 0;
    }
    if (!(quality > 0 && quality <= 1)) {
      LoggerFactory.getLogger(StoreImage.class).warn("Ignoring invalid " + JPEG_QUALITY_PROPERTY + " value '"
          + value + "'. Using " + DEFAULT_JPEG_QUALITY + ".");
      return DEFAULT_JPEG_QUALITY;
    }
    return quality;
  }

  /**
   * Returns the image as the Java internal representation.
   * 
   * @return The image itself.
   */
  public BufferedImage getImage() {
    BufferedImage image = null;
    ByteArrayInputStream stream = new ByteArrayInputStream(Base64.getDecoder().decode(data));
    try {
      image = ImageIO.read(stream);
    } catch (IOException ioException) {
      log.warn("An IOException occured while trying to read image from stream.", ioException);
    }
    return image;
  }

  /**
   * Returns the image size.
   * 
   * @return The image size.
   */
  public ImageSize getSize() {
    return size;
  }

  /**
   * Returns the number of bytes stored in the internal data structure.
   * 
   * @return Number of bytes stored for this image.
   */
  @Override
  public long getByteSize() {
    return data.length;
  }

  /**
   * Returns the base64 encoded byte array as string.
   * 
   * @return Base64 encoded data as string.
   */
  public String getBase64() {
    return new String(data);
  }

  /**
   * Getter for bytearray.
   * @return bytearray
   */
  public byte[] getByteArray() {
    return Arrays.copyOf(data, data.length);
  }

  @Override
  public String toString() {
    String base64 = getBase64();
    if (base64.startsWith(JPEG_BASE64_PREFIX)) {
      return JPEG_DATA_STRING + base64;
    }
    return STORE_IMAGE_DATA_STRING + base64;
  }

  // Auto-generated
  @Override
  public int hashCode() {
    final int prime = 31;
    int result = 1;
    result = prime * result + (int) (id ^ (id >>> 32));
    return result;
  }

  @Override
  public boolean equals(Object obj) {
    if (this == obj) {
      return true;
    }
    if (obj == null) {
      return false;
    }
    if (getClass() != obj.getClass()) {
      return false;
    }
    StoreImage other = (StoreImage) obj;
    if (id != other.getId()) {
      return false;
    }
    return true;
  }
}
