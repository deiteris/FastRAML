Getting started
===============

This guide adds a book to the catalogue and reads it back. It links to the
reference for every detail, rather than repeating it.

Choose your tenant
------------------

Every request goes to your own subdomain, named by the
:raml:base-uri-parameter:`tenant` base URI parameter. See the
:raml:api:`API overview <books>` for the full address.

Sign in
-------

Most calls need an access token from the :raml:security-scheme:`oauth2`
scheme. Server-to-server integrations can use a
:raml:security-scheme:`machine token <machineToken>` instead.

Add a book
----------

Send the new book to :raml:method:`POST /books`. The request looks like this:

.. raml:method:: POST /books
   :detail: request
   :no-index:

   Send the whole book. The store assigns ``id`` and ``createdAt``, and
   ignores any values you give for them.

``:no-index:`` makes this a copy: links to ``POST /books`` still go to its
entry in the reference, not to this page. ``:detail: request`` leaves out the
responses, which a first request does not need.

Read it back
------------

Fetch the book with :raml:method:`GET /books/{isbn}`, using its
:raml:property:`Book.isbn`. A successful call returns
:raml:response:`GET /books/{isbn} 200` with a :raml:type:`Book` in the body.

Next steps
----------

- The :raml:documentation-item:`Pagination` guide explains how lists are paged.
- :raml:endpoint:`/search` finds books by title or author.
- :raml:type:`Anything` lists every body shape this API accepts.
