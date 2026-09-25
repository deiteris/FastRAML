Changelog
=========

v2
--

- Added :raml:method:`DELETE /books/{isbn}`.
- :raml:type:`Book` gained :raml:property:`Book.priceHistory`, typed
  :raml:type:`Prices`.
- :raml:endpoint:`/deliveries` takes a :raml:type:`DeliveryQuery` query string.
- Removed :raml:method:`!GET /books/{isbn}/reviews`. A leading ``!`` names
  something that no longer exists without linking to it, so the build does not
  fail on it.
