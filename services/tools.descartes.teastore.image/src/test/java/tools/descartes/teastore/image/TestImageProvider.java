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
 * Modified in 2026 by Juuso Karjanlahti: added tests for the rendered-image cache.
 */
package tools.descartes.teastore.image;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.awt.image.BufferedImage;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

import tools.descartes.teastore.entities.ImageSize;
import tools.descartes.teastore.entities.ImageSizePreset;
import tools.descartes.teastore.image.cache.IDataCache;
import tools.descartes.teastore.image.storage.IDataStorage;

/**
 * Tests for {@link ImageProvider}, focused on the correctness of its rendered-image response cache: cache
 * hits avoiding storage loads, cache keys not colliding across products/sizes, byte-budget eviction,
 * invalidation, and the runtime enable/disable toggle. Deliberately contains no wall-clock timing
 * assertions, since this suite runs as part of {@code mvn clean install} on every branch push in CI, where
 * timing thresholds would be flaky.
 * @author Norbert Schmitt
 */
public class TestImageProvider {

  private ImageDB db;
  private TrackingStorage storage;

  /**
   * Sets up a fresh image database and tracking storage for the image provider singleton before every test.
   */
  @Before
  public void setUp() {
    db = new ImageDB();
    storage = new TrackingStorage();
    ImageProvider.IP.setImageDB(db);
    ImageProvider.IP.setStorage(storage);
    ImageProvider.IP.invalidateRenderedCache();
  }

  /**
   * Restores the rendered image cache to its default configuration after every test, since
   * {@link ImageProvider} is a shared singleton.
   */
  @After
  public void tearDown() {
    System.clearProperty(ImageProvider.RENDERED_CACHE_ENABLED_PROPERTY);
    System.clearProperty(ImageProvider.RENDERED_CACHE_MAX_BYTES_PROPERTY);
    System.clearProperty(StoreImage.PRODUCT_IMAGE_FORMAT_PROPERTY);
    ImageProvider.IP.setRenderedCacheMaxSize(IDataCache.STD_MAX_CACHE_SIZE);
    ImageProvider.IP.invalidateRenderedCache();
  }

  private void registerImage(long productId, long imageId, ImageSize size, int pixelSize) {
    StoreImage image = new StoreImage(imageId,
        new BufferedImage(pixelSize, pixelSize, BufferedImage.TYPE_INT_ARGB), size);
    db.setImageMapping(productId, imageId, size);
    storage.saveData(image);
  }

  private Map<Long, ImageSize> singleRequest(long productId, ImageSize size) {
    Map<Long, ImageSize> request = new HashMap<>();
    request.put(productId, size);
    return request;
  }

  /**
   * Repeated identical requests must be served from the rendered image cache after the first miss, without
   * requiring another storage load.
   */
  @Test
  public void repeatedIdenticalRequestHitsRenderedCache() {
    ImageSize size = new ImageSize(120, 120);
    registerImage(1L, 101L, size, 120);
    Map<Long, ImageSize> request = singleRequest(1L, size);

    ImageProvider.IP.getProductImages(request);
    ImageProvider.IP.getProductImages(request);
    ImageProvider.IP.getProductImages(request);

    assertEquals("A cached rendered image must not require another storage load.", 1,
        storage.getLoadCallCount());
    assertEquals(2, ImageProvider.IP.getRenderedCacheHitCount());
    assertEquals(1, ImageProvider.IP.getRenderedCacheMissCount());
  }

  /**
   * Requests for different products or different sizes of the same product must not collide on the same
   * cache entry.
   */
  @Test
  public void differentProductsAndSizesDoNotCollideInCache() {
    ImageSize sizeA = new ImageSize(100, 100);
    ImageSize sizeB = new ImageSize(200, 200);
    registerImage(1L, 101L, sizeA, 100);
    registerImage(1L, 102L, sizeB, 200);
    registerImage(2L, 103L, sizeA, 100);

    ImageProvider.IP.getProductImages(singleRequest(1L, sizeA));
    ImageProvider.IP.getProductImages(singleRequest(1L, sizeB));
    ImageProvider.IP.getProductImages(singleRequest(2L, sizeA));

    assertEquals("Three distinct (product, size) pairs must each cause their own storage load.", 3,
        storage.getLoadCallCount());
    assertEquals(3, ImageProvider.IP.getRenderedCacheEntryCount());
  }

  /**
   * Once the cache's byte budget is exceeded, the least recently used entry must be evicted and reported via
   * the eviction counter, while total cache usage stays within the configured budget.
   */
  @Test
  public void cacheEvictsLeastRecentlyUsedEntryWhenByteBudgetExceeded() {
    ImageSize size = new ImageSize(50, 50);
    registerImage(1L, 101L, size, 50);
    registerImage(2L, 102L, size, 50);
    Map<Long, ImageSize> firstRequest = singleRequest(1L, size);
    Map<Long, ImageSize> secondRequest = singleRequest(2L, size);

    ImageProvider.IP.getProductImages(firstRequest);
    long singleEntryBytes = ImageProvider.IP.getRenderedCacheByteSize();
    ImageProvider.IP.setRenderedCacheMaxSize(singleEntryBytes);

    ImageProvider.IP.getProductImages(secondRequest);

    assertEquals("Cache must stay within its configured byte budget.", 1,
        ImageProvider.IP.getRenderedCacheEntryCount());
    assertTrue("Byte usage must not exceed the configured budget.",
        ImageProvider.IP.getRenderedCacheByteSize() <= singleEntryBytes);
    assertEquals(1, ImageProvider.IP.getRenderedCacheEvictionCount());

    ImageProvider.IP.getProductImages(firstRequest);
    assertEquals("The evicted entry must require a fresh storage load.", 3, storage.getLoadCallCount());
  }

  /**
   * Invalidating the rendered image cache (as happens whenever images are regenerated) must force a fresh
   * storage load even for a key that was cached right before the invalidation.
   */
  @Test
  public void invalidateRenderedCacheForcesFreshStorageLoad() {
    ImageSize size = new ImageSize(80, 80);
    registerImage(1L, 101L, size, 80);
    Map<Long, ImageSize> request = singleRequest(1L, size);

    ImageProvider.IP.getProductImages(request);
    ImageProvider.IP.getProductImages(request);
    assertEquals(1, storage.getLoadCallCount());

    ImageProvider.IP.invalidateRenderedCache();
    ImageProvider.IP.getProductImages(request);

    assertEquals("Invalidating the cache must force a fresh storage load for a previously cached key.", 2,
        storage.getLoadCallCount());
  }

  /**
   * Setting the {@link ImageProvider#RENDERED_CACHE_ENABLED_PROPERTY} system property to {@code false} must
   * bypass the rendered image cache entirely, causing every request to hit storage again.
   */
  @Test
  public void disablingRenderedCacheBypassesCaching() {
    System.setProperty(ImageProvider.RENDERED_CACHE_ENABLED_PROPERTY, "false");

    ImageSize size = new ImageSize(60, 60);
    registerImage(1L, 101L, size, 60);
    Map<Long, ImageSize> request = singleRequest(1L, size);

    ImageProvider.IP.getProductImages(request);
    ImageProvider.IP.getProductImages(request);
    ImageProvider.IP.getProductImages(request);

    assertEquals("With the cache disabled, every request must hit storage again.", 3,
        storage.getLoadCallCount());
    assertEquals(0, ImageProvider.IP.getRenderedCacheEntryCount());
  }

  /**
   * The {@link ImageProvider#RENDERED_CACHE_MAX_BYTES_PROPERTY} system property sets the cache budget, so a budget
   * sweep can run against one binary.
   */
  @Test
  public void maxBytesPropertySetsRenderedCacheBudget() {
    System.setProperty(ImageProvider.RENDERED_CACHE_MAX_BYTES_PROPERTY, "8388608");
    assertEquals(8388608L, ImageProvider.configuredRenderedCacheMaxBytes());

    System.setProperty(ImageProvider.RENDERED_CACHE_MAX_BYTES_PROPERTY, " 1048576 ");
    assertEquals("Surrounding whitespace from a compose file must not matter.", 1048576L,
        ImageProvider.configuredRenderedCacheMaxBytes());
  }

  /**
   * An unset, malformed or non-positive budget falls back to the default instead of failing startup.
   */
  @Test
  public void invalidMaxBytesPropertyFallsBackToDefault() {
    assertEquals(IDataCache.STD_MAX_CACHE_SIZE, ImageProvider.configuredRenderedCacheMaxBytes());
    for (String invalid : new String[] {"", "8MiB", "0", "-1"}) {
      System.setProperty(ImageProvider.RENDERED_CACHE_MAX_BYTES_PROPERTY, invalid);
      assertEquals("Value '" + invalid + "' must fall back to the default.", IDataCache.STD_MAX_CACHE_SIZE,
          ImageProvider.configuredRenderedCacheMaxBytes());
    }
  }

  /**
   * With product images set to JPEG, a product image scaled to a new size is stored and served as JPEG, while a
   * web interface image scaled the same way stays PNG.
   */
  @Test
  public void scaledProductImagesFollowFormatButWebImagesStayPng() {
    System.setProperty(StoreImage.PRODUCT_IMAGE_FORMAT_PROPERTY, StoreImage.JPEG_FORMAT);
    ImageSize stdSize = ImageSizePreset.STD_IMAGE_SIZE;
    registerImage(1L, 101L, stdSize, 30);
    db.setImageMapping("logo", 201L, stdSize);
    storage.saveData(new StoreImage(201L, new BufferedImage(30, 30, BufferedImage.TYPE_INT_ARGB), stdSize));

    ImageSize size = new ImageSize(40, 31);
    String product = ImageProvider.IP.getProductImages(singleRequest(1L, size)).get(1L);
    Map<String, ImageSize> webRequest = new HashMap<>();
    webRequest.put("logo", size);
    String webImage = ImageProvider.IP.getWebUIImages(webRequest).get("logo");

    assertTrue("A scaled product image must be JPEG.", product.startsWith("data:image/jpeg;base64,/9j/"));
    assertTrue("A scaled web image must stay PNG.", webImage.startsWith("data:image/png;base64,"));
  }

  /**
   * In-memory {@link IDataStorage} used to track how many times {@link #loadData(long)} is invoked, without
   * touching the file system.
   * @author Norbert Schmitt
   */
  static final class TrackingStorage implements IDataStorage<StoreImage> {
    private final Map<Long, StoreImage> data = new HashMap<>();
    private final AtomicLong loadCount = new AtomicLong();

    @Override
    public boolean dataExists(long id) {
      return data.containsKey(id);
    }

    @Override
    public StoreImage loadData(long id) {
      loadCount.incrementAndGet();
      return data.get(id);
    }

    @Override
    public boolean saveData(StoreImage image) {
      if (image == null) {
        return false;
      }
      data.put(image.getId(), image);
      return true;
    }

    @Override
    public boolean dataIsStorable(StoreImage image) {
      return true;
    }

    @Override
    public boolean deleteData(StoreImage image) {
      return data.remove(image.getId()) != null;
    }

    /**
     * Returns the number of times {@link #loadData(long)} has been called.
     * @return Number of load calls.
     */
    long getLoadCallCount() {
      return loadCount.get();
    }
  }
}
