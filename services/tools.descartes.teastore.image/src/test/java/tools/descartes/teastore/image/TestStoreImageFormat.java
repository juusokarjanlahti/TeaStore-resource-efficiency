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
 */
package tools.descartes.teastore.image;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.Base64;
import java.util.Random;

import javax.imageio.ImageIO;

import org.junit.After;
import org.junit.Test;

import tools.descartes.teastore.entities.ImageSize;

/**
 * Tests for the product image format switch in {@link StoreImage}: choosing the format from system properties,
 * encoding JPEG from the translucent images the scaler produces, and labelling each data URI with the format its
 * bytes are actually in.
 * @author Norbert Schmitt
 */
public class TestStoreImageFormat {

  /**
   * Clears the format properties after every test, since system properties are shared JVM-wide.
   */
  @After
  public void tearDown() {
    System.clearProperty(StoreImage.PRODUCT_IMAGE_FORMAT_PROPERTY);
    System.clearProperty(StoreImage.JPEG_QUALITY_PROPERTY);
  }

  private static BufferedImage noisyImage(int type) {
    BufferedImage image = new BufferedImage(120, 90, type);
    Random random = new Random(7);
    for (int x = 0; x < image.getWidth(); x++) {
      for (int y = 0; y < image.getHeight(); y++) {
        image.setRGB(x, y, 0xFF000000 | random.nextInt(0x1000000));
      }
    }
    return image;
  }

  private static BufferedImage decode(byte[] base64) throws IOException {
    return ImageIO.read(new ByteArrayInputStream(Base64.getDecoder().decode(base64)));
  }

  /**
   * Product images stay PNG unless the property asks for JPEG; unknown values fall back to PNG.
   */
  @Test
  public void productFormatDefaultsToPngAndAcceptsJpeg() {
    assertEquals(StoreImage.STORE_IMAGE_FORMAT, StoreImage.productImageFormat());
    System.setProperty(StoreImage.PRODUCT_IMAGE_FORMAT_PROPERTY, " JPG ");
    assertEquals(StoreImage.JPEG_FORMAT, StoreImage.productImageFormat());
    System.setProperty(StoreImage.PRODUCT_IMAGE_FORMAT_PROPERTY, "jpeg");
    assertEquals(StoreImage.JPEG_FORMAT, StoreImage.productImageFormat());
    System.setProperty(StoreImage.PRODUCT_IMAGE_FORMAT_PROPERTY, "webp");
    assertEquals(StoreImage.STORE_IMAGE_FORMAT, StoreImage.productImageFormat());
  }

  /**
   * An unset, malformed or out-of-range JPEG quality falls back to the default.
   */
  @Test
  public void jpegQualityFallsBackToDefault() {
    assertEquals(StoreImage.DEFAULT_JPEG_QUALITY, StoreImage.jpegQuality(), 0f);
    System.setProperty(StoreImage.JPEG_QUALITY_PROPERTY, "0.6");
    assertEquals(0.6f, StoreImage.jpegQuality(), 0f);
    for (String invalid : new String[] {"high", "0", "1.5", "-0.2", "NaN"}) {
      System.setProperty(StoreImage.JPEG_QUALITY_PROPERTY, invalid);
      assertEquals("Value '" + invalid + "' must fall back to the default.", StoreImage.DEFAULT_JPEG_QUALITY,
          StoreImage.jpegQuality(), 0f);
    }
  }

  /**
   * The scaler produces translucent images, which ImageIO's JPEG writer rejects. Encoding one as JPEG must still
   * produce a real, decodable JPEG of the same dimensions rather than an empty image.
   * @throws IOException If the encoded image cannot be decoded.
   */
  @Test
  public void translucentImageEncodesAsDecodableJpeg() throws IOException {
    byte[] base64 = StoreImage.encode(noisyImage(BufferedImage.TYPE_INT_ARGB_PRE), StoreImage.JPEG_FORMAT);

    assertTrue("Encoded data must start with the JPEG marker.", new String(base64).startsWith("/9j/"));
    BufferedImage decoded = decode(base64);
    assertNotNull(decoded);
    assertEquals(120, decoded.getWidth());
    assertEquals(90, decoded.getHeight());
  }

  /**
   * A lower JPEG quality must produce a smaller image, so the quality property actually reaches the writer.
   */
  @Test
  public void lowerJpegQualityProducesSmallerImage() {
    BufferedImage image = noisyImage(BufferedImage.TYPE_INT_RGB);
    System.setProperty(StoreImage.JPEG_QUALITY_PROPERTY, "0.9");
    int high = StoreImage.encode(image, StoreImage.JPEG_FORMAT).length;
    System.setProperty(StoreImage.JPEG_QUALITY_PROPERTY, "0.3");
    int low = StoreImage.encode(image, StoreImage.JPEG_FORMAT).length;
    assertTrue("Quality 0.3 (" + low + " B) must be smaller than 0.9 (" + high + " B).", low < high);
  }

  /**
   * The data URI must name the format the bytes are in, whichever way the image was created or loaded.
   */
  @Test
  public void dataUriNamesTheActualFormat() {
    ImageSize size = new ImageSize(120, 90);
    BufferedImage image = noisyImage(BufferedImage.TYPE_INT_RGB);
    StoreImage png = new StoreImage(1L, image, size);
    StoreImage jpeg = new StoreImage(2L, image, size, StoreImage.JPEG_FORMAT);
    StoreImage loadedJpeg = new StoreImage(3L, jpeg.getByteArray(), size);

    assertTrue(png.toString().startsWith("data:image/png;base64,iVBORw0KGgo"));
    assertTrue(jpeg.toString().startsWith("data:image/jpeg;base64,/9j/"));
    assertTrue(loadedJpeg.toString().startsWith("data:image/jpeg;base64,/9j/"));
  }

  /**
   * An unknown format must fail loudly instead of storing an empty image.
   */
  @Test(expected = IllegalArgumentException.class)
  public void unknownFormatIsRejected() {
    StoreImage.encode(noisyImage(BufferedImage.TYPE_INT_RGB), "no-such-format");
  }
}
