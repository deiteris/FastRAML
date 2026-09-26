Getting started
===============

This guide adds a book to the catalogue and reads it back. Each step shows
what to send and what comes back, in place; the reference has the rest.

Choose your tenant
------------------

Every request goes to your own subdomain, named by the
:raml:base-uri-parameter:`tenant` base URI parameter. This guide uses
``acme``.

Sign in
-------

Most calls need an access token from the :raml:security-scheme:`oauth2`
scheme. Server-to-server integrations can use a
:raml:security-scheme:`machine token <machineToken>` instead.

Add a book
----------

Send the whole book as JSON. You choose its ``id`` and ``createdAt``.

.. raml:send:: POST /books
   :values:
      tenant = acme
      Authorization = Bearer <your token>
   :body: new-book.json

The store answers with the book as it stored it, whose fields are the ones
you just sent. ``Location`` says where it now lives.

.. raml:expect:: POST /books 201
   :values:
      Location = /books/9780061054884
   :body: new-book.json
   :fields: none

Read it back
------------

Ask for the book by its ISBN.

.. raml:send:: GET /books/{isbn}
   :values:
      tenant = acme
      Authorization = Bearer <your token>
      isbn = 9780061054884

The book comes back as JSON.

.. raml:expect:: GET /books/{isbn} 200
   :body: new-book.json

Next steps
----------

- The :raml:documentation-item:`Pagination` guide explains how lists are paged.
- :raml:endpoint:`/search` finds books by title or author.
- :raml:type:`Anything` lists every body shape this API accepts.
