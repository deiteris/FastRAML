/** Every viewer route over a document supplied by the shell. */

import { useLocation, Route, Routes } from 'react-router';
import type { Document, Index } from '../model';
import { Boundary } from '../components/ui';
import {
  AnnotationTypeList,
  AnnotationTypePage,
  DocumentationList,
  DocumentationPage,
  EndpointPage,
  NotFound,
  OperationPage,
  Overview,
  ResolveAddress,
  SecurityList,
  SecuritySchemePage,
  TypeList,
  TypePage,
} from './index';

/** Remount on route changes so disclosures and error boundaries start fresh. */
export function Pages({ document, index }: { document: Document; index: Index }) {
  const pages = { document, index };
  const { pathname } = useLocation();
  return (
    <Boundary key={pathname} what="page">
      <Routes>
        <Route path="/" element={<Overview {...pages} />} />
        <Route path="/documentation" element={<DocumentationList {...pages} />} />
        <Route path="/documentation/:at" element={<DocumentationPage {...pages} />} />
        <Route path="/endpoints/:path" element={<EndpointPage {...pages} />} />
        <Route path="/endpoints/:path/:method" element={<OperationPage {...pages} />} />
        <Route path="/types" element={<TypeList {...pages} />} />
        <Route path="/types/:file/:name" element={<TypePage {...pages} />} />
        <Route path="/annotation-types" element={<AnnotationTypeList {...pages} />} />
        <Route path="/annotation-types/:file/:name" element={<AnnotationTypePage {...pages} />} />
        <Route path="/security" element={<SecurityList {...pages} />} />
        <Route path="/security/:file/:name" element={<SecuritySchemePage {...pages} />} />
        <Route path="/n/:address" element={<ResolveAddress {...pages} />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Boundary>
  );
}
